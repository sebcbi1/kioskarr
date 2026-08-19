from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from kioskarr.app_settings import get_app_settings
from kioskarr.db import get_db
from kioskarr.jobs.search_job import grab_release_candidate, run_search_job
from kioskarr.matcher import is_already_in_flight, issue_already_owned
from kioskarr.models import FormatPreference, GrabStatus, Publication, PublicationType
from kioskarr.parser import parse
from kioskarr.prowlarr_client import ProwlarrClient, Release
from kioskarr.qbittorrent_client import QBittorrentClient

router = APIRouter(prefix="/publications", tags=["publications"])


class PublicationCreate(BaseModel):
    title: str
    type: PublicationType = PublicationType.magazine
    aliases: list[str] = []
    format_preference: FormatPreference = FormatPreference.any
    min_seeders: int = 1
    target_dir: str
    monitored: bool = True
    grab_last_n: int = 1


class PublicationUpdate(BaseModel):
    title: str | None = None
    aliases: list[str] | None = None
    format_preference: FormatPreference | None = None
    min_seeders: int | None = None
    target_dir: str | None = None
    monitored: bool | None = None
    grab_last_n: int | None = None
    # Manual override/reset of the cold-start floor — e.g. to say "only monitor
    # issues after this one" without waiting for the next search cycle, or to
    # clear it back to None to force a cold-start re-evaluation.
    baseline_identifier: str | None = None


class PublicationOut(BaseModel):
    id: int
    title: str
    type: PublicationType
    aliases: list[str]
    format_preference: FormatPreference
    min_seeders: int
    target_dir: str
    monitored: bool
    grab_last_n: int
    baseline_identifier: str | None

    model_config = {"from_attributes": True}


@router.post("", response_model=PublicationOut, status_code=201)
def create_publication(payload: PublicationCreate, db: Session = Depends(get_db)) -> Publication:
    publication = Publication(**payload.model_dump())
    db.add(publication)
    db.commit()
    db.refresh(publication)
    return publication


@router.get("", response_model=list[PublicationOut])
def list_publications(db: Session = Depends(get_db)) -> list[Publication]:
    return db.query(Publication).all()


@router.get("/{publication_id}", response_model=PublicationOut)
def get_publication(publication_id: int, db: Session = Depends(get_db)) -> Publication:
    publication = db.get(Publication, publication_id)
    if publication is None:
        raise HTTPException(404, "Publication not found")
    return publication


@router.patch("/{publication_id}", response_model=PublicationOut)
def update_publication(
    publication_id: int, payload: PublicationUpdate, db: Session = Depends(get_db)
) -> Publication:
    publication = db.get(Publication, publication_id)
    if publication is None:
        raise HTTPException(404, "Publication not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(publication, field, value)
    db.commit()
    db.refresh(publication)
    return publication


@router.delete("/{publication_id}", status_code=204)
def delete_publication(publication_id: int, db: Session = Depends(get_db)) -> None:
    publication = db.get(Publication, publication_id)
    if publication is None:
        raise HTTPException(404, "Publication not found")
    db.delete(publication)
    db.commit()


class ManualGrabRequest(BaseModel):
    title: str
    guid: str
    download_url: str
    indexer_id: int | None = None
    indexer_name: str | None = None
    seeders: int | None = None
    size: int | None = None
    info_hash: str | None = None


class ManualGrabOut(BaseModel):
    id: int
    release_title: str
    identifier: str
    status: GrabStatus
    torrent_hash: str | None
    already_owned: bool
    already_in_flight: bool


@router.post("/{publication_id}/grab-release", response_model=ManualGrabOut, status_code=201)
def grab_release(
    publication_id: int, payload: ManualGrabRequest, db: Session = Depends(get_db)
) -> ManualGrabOut:
    """Manually grab one specific search-preview result, bypassing every bit of
    the automatic job's eligibility gating (confidence threshold, min_seeders,
    cold-start baseline) — an explicit, deliberate user choice. Already-owned
    or already-in-flight duplicates are annotated in the response but never
    block the grab, matching Radarr/Sonarr's own manual-search behavior (e.g.
    re-grabbing to replace a bad prior download).
    """
    publication = db.get(Publication, publication_id)
    if publication is None:
        raise HTTPException(404, "Publication not found")

    parsed = parse(payload.title)
    if parsed.identifier is None:
        raise HTTPException(
            400, "Couldn't determine an issue date/number from this release title — cannot grab it."
        )

    already_owned = issue_already_owned(db, publication_id, parsed.identifier)
    already_in_flight = is_already_in_flight(db, publication_id, parsed.identifier)

    app_settings = get_app_settings(db)
    try:
        app_settings.require_download_client()
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    qbt = QBittorrentClient(
        app_settings.qbittorrent_url, app_settings.qbittorrent_username, app_settings.qbittorrent_password
    )
    qbt.login()
    qbt.ensure_category(app_settings.qbittorrent_category)

    release = Release(
        title=payload.title,
        guid=payload.guid,
        download_url=payload.download_url,
        indexer_id=payload.indexer_id,
        indexer_name=payload.indexer_name,
        seeders=payload.seeders,
        size=payload.size,
        protocol=None,
        info_hash=payload.info_hash,
    )
    existing_hashes = {t["hash"] for t in qbt.list_torrents()}
    try:
        grab = grab_release_candidate(db, qbt, publication, release, parsed, existing_hashes, app_settings)
    except Exception as exc:
        raise HTTPException(502, f"Failed to add torrent: {exc}") from exc
    db.commit()
    db.refresh(grab)

    return ManualGrabOut(
        id=grab.id,
        release_title=grab.release_title,
        identifier=grab.identifier,
        status=grab.status,
        torrent_hash=grab.torrent_hash,
        already_owned=already_owned,
        already_in_flight=already_in_flight,
    )


@router.post("/{publication_id}/search-now")
def search_now(publication_id: int, db: Session = Depends(get_db)) -> dict:
    publication = db.get(Publication, publication_id)
    if publication is None:
        raise HTTPException(404, "Publication not found")

    app_settings = get_app_settings(db)
    app_settings.require_download_client()
    prowlarr = ProwlarrClient(app_settings.prowlarr_url, app_settings.prowlarr_api_key)
    qbt = QBittorrentClient(
        app_settings.qbittorrent_url, app_settings.qbittorrent_username, app_settings.qbittorrent_password
    )
    qbt.login()
    qbt.ensure_category(app_settings.qbittorrent_category)

    grabs = run_search_job(db, prowlarr, qbt, publications=[publication])
    return {"grabbed": len(grabs), "releases": [g.release_title for g in grabs]}

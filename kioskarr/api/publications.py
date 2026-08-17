from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from kioskarr.app_settings import get_app_settings
from kioskarr.db import get_db
from kioskarr.jobs.search_job import run_search_job
from kioskarr.models import FormatPreference, Publication, PublicationType
from kioskarr.prowlarr_client import ProwlarrClient
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

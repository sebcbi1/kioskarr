from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from magazinerr.config import settings
from magazinerr.db import get_db
from magazinerr.jobs.search_job import run_search_job
from magazinerr.models import FormatPreference, Publication, PublicationType
from magazinerr.prowlarr_client import ProwlarrClient
from magazinerr.qbittorrent_client import QBittorrentClient

router = APIRouter(prefix="/publications", tags=["publications"])


class PublicationCreate(BaseModel):
    title: str
    type: PublicationType = PublicationType.magazine
    aliases: list[str] = []
    format_preference: FormatPreference = FormatPreference.any
    min_seeders: int = 1
    target_dir: str
    monitored: bool = True


class PublicationUpdate(BaseModel):
    title: str | None = None
    aliases: list[str] | None = None
    format_preference: FormatPreference | None = None
    min_seeders: int | None = None
    target_dir: str | None = None
    monitored: bool | None = None


class PublicationOut(BaseModel):
    id: int
    title: str
    type: PublicationType
    aliases: list[str]
    format_preference: FormatPreference
    min_seeders: int
    target_dir: str
    monitored: bool

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

    settings.require_download_client()
    prowlarr = ProwlarrClient(settings.prowlarr_url, settings.prowlarr_api_key)
    qbt = QBittorrentClient(
        settings.qbittorrent_url, settings.qbittorrent_username, settings.qbittorrent_password
    )
    qbt.login()
    qbt.ensure_category(settings.qbittorrent_category)

    grabs = run_search_job(db, prowlarr, qbt, publications=[publication])
    return {"grabbed": len(grabs), "releases": [g.release_title for g in grabs]}

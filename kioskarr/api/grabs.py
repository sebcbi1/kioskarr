from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from kioskarr.db import get_db
from kioskarr.models import Grab, GrabStatus

router = APIRouter(prefix="/grabs", tags=["grabs"])


class GrabOut(BaseModel):
    id: int
    publication_id: int
    publication_title: str
    release_title: str
    identifier: str
    status: GrabStatus
    torrent_hash: str | None
    indexer_id: str | None

    model_config = {"from_attributes": True}


class GrabStatusUpdate(BaseModel):
    status: GrabStatus


def _to_out(grab: Grab) -> GrabOut:
    return GrabOut(
        id=grab.id,
        publication_id=grab.publication_id,
        publication_title=grab.publication.title,
        release_title=grab.release_title,
        identifier=grab.identifier,
        status=grab.status,
        torrent_hash=grab.torrent_hash,
        indexer_id=grab.indexer_id,
    )


@router.get("", response_model=list[GrabOut])
def list_grabs(status: GrabStatus | None = None, db: Session = Depends(get_db)) -> list[GrabOut]:
    query = db.query(Grab)
    if status is not None:
        query = query.filter(Grab.status == status)
    grabs = query.order_by(Grab.created_at.desc()).all()
    return [_to_out(grab) for grab in grabs]


@router.patch("/{grab_id}", response_model=GrabOut)
def update_grab_status(
    grab_id: int, payload: GrabStatusUpdate, db: Session = Depends(get_db)
) -> GrabOut:
    """Manual status override — a debug/escape-hatch tool with no business-rule
    gating beyond "is this a valid status", for grabs stuck in a state the
    automatic jobs can't resolve on their own (e.g. a phantom "downloading"
    grab with no matching torrent in qBittorrent). Also the basis for the
    frontend's "Regrab" action: mark failed here, then re-trigger search-now.
    """
    grab = db.get(Grab, grab_id)
    if grab is None:
        raise HTTPException(404, "Grab not found")
    grab.status = payload.status
    db.commit()
    db.refresh(grab)
    return _to_out(grab)

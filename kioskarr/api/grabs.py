from fastapi import APIRouter, Depends
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


@router.get("", response_model=list[GrabOut])
def list_grabs(status: GrabStatus | None = None, db: Session = Depends(get_db)) -> list[GrabOut]:
    query = db.query(Grab)
    if status is not None:
        query = query.filter(Grab.status == status)
    grabs = query.order_by(Grab.created_at.desc()).all()
    return [
        GrabOut(
            id=grab.id,
            publication_id=grab.publication_id,
            publication_title=grab.publication.title,
            release_title=grab.release_title,
            identifier=grab.identifier,
            status=grab.status,
            torrent_hash=grab.torrent_hash,
            indexer_id=grab.indexer_id,
        )
        for grab in grabs
    ]

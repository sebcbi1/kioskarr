from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from kioskarr.db import get_db
from kioskarr.models import Grab, GrabStatus

router = APIRouter(prefix="/grabs", tags=["grabs"])


class GrabOut(BaseModel):
    id: int
    publication_id: int
    release_title: str
    identifier: str
    status: GrabStatus
    torrent_hash: str | None
    indexer_id: str | None

    model_config = {"from_attributes": True}


@router.get("", response_model=list[GrabOut])
def list_grabs(status: GrabStatus | None = None, db: Session = Depends(get_db)) -> list[Grab]:
    query = db.query(Grab)
    if status is not None:
        query = query.filter(Grab.status == status)
    return query.order_by(Grab.created_at.desc()).all()

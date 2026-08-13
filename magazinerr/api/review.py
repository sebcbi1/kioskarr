from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from magazinerr.db import get_db
from magazinerr.jobs.import_job import import_issue
from magazinerr.models import Publication, ReviewItem

router = APIRouter(prefix="/review", tags=["review"])


class ReviewItemOut(BaseModel):
    id: int
    grab_id: int
    file_path: str
    reason: str
    candidate_publication_id: int | None
    resolved: bool

    model_config = {"from_attributes": True}


class ResolveReviewItem(BaseModel):
    publication_id: int
    identifier: str


@router.get("", response_model=list[ReviewItemOut])
def list_review_items(db: Session = Depends(get_db)) -> list[ReviewItem]:
    return db.query(ReviewItem).filter(ReviewItem.resolved.is_(False)).all()


@router.post("/{review_item_id}/resolve", response_model=ReviewItemOut)
def resolve_review_item(
    review_item_id: int, payload: ResolveReviewItem, db: Session = Depends(get_db)
) -> ReviewItem:
    item = db.get(ReviewItem, review_item_id)
    if item is None:
        raise HTTPException(404, "Review item not found")
    publication = db.get(Publication, payload.publication_id)
    if publication is None:
        raise HTTPException(404, "Publication not found")

    import_issue(db, item.grab, Path(item.file_path), payload.identifier, publication)
    item.resolved = True
    db.commit()
    db.refresh(item)
    return item

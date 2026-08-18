from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from kioskarr.db import get_db
from kioskarr.jobs.import_job import import_issue
from kioskarr.models import Publication, ReviewItem

router = APIRouter(prefix="/review", tags=["review"])


class ReviewItemOut(BaseModel):
    id: int
    grab_id: int
    file_path: str
    reason: str
    candidate_publication_id: int | None
    resolved: bool
    release_title: str

    model_config = {"from_attributes": True}


def _to_out(item: ReviewItem) -> ReviewItemOut:
    return ReviewItemOut(
        id=item.id,
        grab_id=item.grab_id,
        file_path=item.file_path,
        reason=item.reason,
        candidate_publication_id=item.candidate_publication_id,
        resolved=item.resolved,
        release_title=item.grab.release_title,
    )


class ResolveReviewItem(BaseModel):
    publication_id: int
    identifier: str
    # Override the source path recorded at review-creation time — needed when
    # that path was never actually known (e.g. a duplicate-torrent grab has no
    # file_path to start with) or turned out to be wrong.
    file_path: str | None = None


@router.get("", response_model=list[ReviewItemOut])
def list_review_items(db: Session = Depends(get_db)) -> list[ReviewItemOut]:
    items = db.query(ReviewItem).filter(ReviewItem.resolved.is_(False)).all()
    return [_to_out(item) for item in items]


@router.post("/{review_item_id}/resolve", response_model=ReviewItemOut)
def resolve_review_item(
    review_item_id: int, payload: ResolveReviewItem, db: Session = Depends(get_db)
) -> ReviewItemOut:
    item = db.get(ReviewItem, review_item_id)
    if item is None:
        raise HTTPException(404, "Review item not found")
    publication = db.get(Publication, payload.publication_id)
    if publication is None:
        raise HTTPException(404, "Publication not found")

    source_path = Path(payload.file_path) if payload.file_path else Path(item.file_path)
    try:
        import_issue(db, item.grab, source_path, payload.identifier, publication)
    except OSError as exc:
        raise HTTPException(
            400,
            f"Couldn't import from {source_path}: {exc}. This path is resolved from inside "
            "this process/container, not necessarily what qBittorrent's own UI shows — e.g. "
            "/downloads/... here if that's how it's mounted, not the host's own /mnt/... path.",
        ) from exc
    item.resolved = True
    db.commit()
    db.refresh(item)
    return _to_out(item)

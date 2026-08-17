"""Server-rendered Review Queue page. Reuses api.review.resolve_review_item
directly rather than re-implementing the resolve logic."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from kioskarr.api.review import ResolveReviewItem, resolve_review_item
from kioskarr.db import get_db
from kioskarr.models import Publication, ReviewItem
from kioskarr.templating import templates
from kioskarr.ui.publications import _redirect

router = APIRouter(prefix="/ui/review", tags=["ui"])


@router.get("")
def list_review_page(request: Request, db: Session = Depends(get_db)):
    items = (
        db.query(ReviewItem)
        .filter(ReviewItem.resolved.is_(False))
        .order_by(ReviewItem.created_at.desc())
        .all()
    )
    publications = db.query(Publication).order_by(Publication.title).all()
    return templates.TemplateResponse(
        request,
        "review_list.html",
        {
            "active_nav": "review",
            "items": items,
            "publications": publications,
            "flash": request.query_params.get("flash"),
            "flash_type": request.query_params.get("flash_type"),
        },
    )


@router.post("/{review_item_id}/resolve")
async def resolve_review_item_submit(review_item_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    payload = ResolveReviewItem(
        publication_id=int(form["publication_id"]),
        identifier=form["identifier"],
        file_path=form.get("file_path") or None,
    )
    try:
        resolve_review_item(review_item_id, payload, db)
    except Exception as exc:
        return _redirect("/ui/review", f"Failed to resolve: {exc}", "error")
    return _redirect("/ui/review", "Review item resolved")

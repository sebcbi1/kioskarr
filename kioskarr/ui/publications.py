"""Server-rendered Publications pages. Route handlers reuse the existing
api.publications functions directly (as plain Python calls) rather than
re-implementing create/update/delete/search-now logic.
"""

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from kioskarr.api.publications import (
    PublicationCreate,
    PublicationUpdate,
    create_publication,
    delete_publication,
    search_now,
    update_publication,
)
from kioskarr.db import get_db
from kioskarr.models import FormatPreference, Publication, PublicationType
from kioskarr.templating import templates

router = APIRouter(prefix="/ui/publications", tags=["ui"])


def _redirect(url: str, flash: str, flash_type: str = "success") -> RedirectResponse:
    query = urlencode({"flash": flash, "flash_type": flash_type})
    return RedirectResponse(url=f"{url}?{query}", status_code=303)


def _aliases_from_form(form) -> list[str]:
    return [a.strip() for a in form.getlist("aliases") if a.strip()]


@router.get("")
def list_publications_page(request: Request, db: Session = Depends(get_db)):
    publications = db.query(Publication).order_by(Publication.title).all()
    return templates.TemplateResponse(
        request,
        "publications_list.html",
        {
            "active_nav": "publications",
            "publications": publications,
            "flash": request.query_params.get("flash"),
            "flash_type": request.query_params.get("flash_type"),
        },
    )


@router.get("/new")
def new_publication_page(request: Request):
    return templates.TemplateResponse(
        request,
        "publication_form.html",
        {
            "active_nav": "publications",
            "publication": None,
            "types": list(PublicationType),
            "formats": list(FormatPreference),
        },
    )


@router.post("/new")
async def create_publication_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    payload = PublicationCreate(
        title=form["title"],
        type=form["type"],
        aliases=_aliases_from_form(form),
        format_preference=form["format_preference"],
        min_seeders=int(form.get("min_seeders") or 1),
        target_dir=form["target_dir"],
        monitored="monitored" in form,
        grab_last_n=int(form.get("grab_last_n") or 1),
    )
    create_publication(payload, db)
    return _redirect("/ui/publications", f'Added "{payload.title}"')


@router.get("/{publication_id}/edit")
def edit_publication_page(publication_id: int, request: Request, db: Session = Depends(get_db)):
    publication = db.get(Publication, publication_id)
    if publication is None:
        raise HTTPException(404, "Publication not found")
    return templates.TemplateResponse(
        request,
        "publication_form.html",
        {
            "active_nav": "publications",
            "publication": publication,
            "types": list(PublicationType),
            "formats": list(FormatPreference),
        },
    )


@router.post("/{publication_id}/edit")
async def update_publication_submit(publication_id: int, request: Request, db: Session = Depends(get_db)):
    if db.get(Publication, publication_id) is None:
        raise HTTPException(404, "Publication not found")
    form = await request.form()
    payload = PublicationUpdate(
        title=form["title"],
        aliases=_aliases_from_form(form),
        format_preference=form["format_preference"],
        min_seeders=int(form.get("min_seeders") or 1),
        target_dir=form["target_dir"],
        monitored="monitored" in form,
        grab_last_n=int(form.get("grab_last_n") or 1),
    )
    update_publication(publication_id, payload, db)
    return _redirect("/ui/publications", f'Updated "{payload.title}"')


@router.post("/{publication_id}/delete")
def delete_publication_submit(publication_id: int, db: Session = Depends(get_db)):
    publication = db.get(Publication, publication_id)
    if publication is None:
        raise HTTPException(404, "Publication not found")
    title = publication.title
    delete_publication(publication_id, db)
    return _redirect("/ui/publications", f'Deleted "{title}"')


@router.post("/{publication_id}/search-now")
def search_now_submit(publication_id: int, db: Session = Depends(get_db)):
    try:
        result = search_now(publication_id, db)
    except Exception as exc:
        return _redirect("/ui/publications", f"Search failed: {exc}", "error")
    if result["grabbed"]:
        return _redirect("/ui/publications", f"Grabbed {result['grabbed']} release(s)")
    return _redirect("/ui/publications", "No new releases found", "error")


@router.post("/{publication_id}/reset-baseline")
def reset_baseline_submit(publication_id: int, db: Session = Depends(get_db)):
    if db.get(Publication, publication_id) is None:
        raise HTTPException(404, "Publication not found")
    update_publication(publication_id, PublicationUpdate(baseline_identifier=None), db)
    return _redirect(
        f"/ui/publications/{publication_id}/edit",
        "Baseline reset — next search will cold-start again",
    )

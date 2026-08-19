"""Ad-hoc, read-only search preview — hits Prowlarr and shows what the parser/matcher
would see, without grabbing anything or touching the database. Useful for checking
indexer coverage and parsing quality for a title before adding it as a Publication,
and reused by the publication form's Preview/Refresh buttons: the title/aliases sent
here are whatever's currently in the form, including edits not yet saved, so a user
can see how a tweaked alias affects matching before committing to it.
"""

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from kioskarr.app_settings import get_app_settings
from kioskarr.db import get_db
from kioskarr.jobs.search_job import collect_candidates
from kioskarr.matcher import is_already_in_flight, issue_already_owned, title_match_score
from kioskarr.parser import identifier_sort_key, parse
from kioskarr.prowlarr_client import ProwlarrClient

router = APIRouter(prefix="/search", tags=["search"])


class ParsedOut(BaseModel):
    title_guess: str
    identifier: str | None
    identifier_kind: str | None
    format_ext: str | None


class MatchOut(BaseModel):
    score: float
    meets_threshold: bool
    already_owned: bool
    already_in_flight: bool


class ReleasePreview(BaseModel):
    title: str
    guid: str
    download_url: str
    indexer_id: int | None
    indexer_name: str | None
    seeders: int | None
    size: int | None
    info_hash: str | None
    parsed: ParsedOut
    match: MatchOut


@router.get("/preview", response_model=list[ReleasePreview])
def preview_search(
    title: str,
    aliases: list[str] = Query(default=[]),
    publication_id: int | None = None,
    db: Session = Depends(get_db),
) -> list[ReleasePreview]:
    app_settings = get_app_settings(db)
    try:
        app_settings.require_prowlarr()
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc

    prowlarr = ProwlarrClient(app_settings.prowlarr_url, app_settings.prowlarr_api_key)
    try:
        releases = collect_candidates(prowlarr, [title, *aliases])
    except requests.RequestException as exc:
        raise HTTPException(502, f"Prowlarr search failed: {exc}") from exc

    previews = []
    for release in releases:
        parsed = parse(release.title)
        score = title_match_score(parsed, title, aliases)
        already_owned = already_in_flight = False
        if publication_id is not None and parsed.identifier is not None:
            already_owned = issue_already_owned(db, publication_id, parsed.identifier)
            already_in_flight = is_already_in_flight(db, publication_id, parsed.identifier)
        previews.append(
            ReleasePreview(
                title=release.title,
                guid=release.guid,
                download_url=release.download_url,
                indexer_id=release.indexer_id,
                indexer_name=release.indexer_name,
                seeders=release.seeders,
                size=release.size,
                info_hash=release.info_hash,
                parsed=ParsedOut(
                    title_guess=parsed.title_guess,
                    identifier=parsed.identifier,
                    identifier_kind=parsed.identifier_kind,
                    format_ext=parsed.format_ext,
                ),
                match=MatchOut(
                    score=score,
                    meets_threshold=score >= app_settings.match_confidence_threshold,
                    already_owned=already_owned,
                    already_in_flight=already_in_flight,
                ),
            )
        )

    # Newest issue first — Prowlarr's own ordering doesn't reliably reflect
    # issue date, and that's what a user scanning results actually wants.
    # Releases the parser couldn't date at all sort after every dated one,
    # in whatever order Prowlarr returned them (nothing to rank them by).
    previews.sort(
        key=lambda p: (
            p.parsed.identifier is not None,
            identifier_sort_key(p.parsed.identifier) if p.parsed.identifier is not None else (0, ""),
        ),
        reverse=True,
    )
    return previews

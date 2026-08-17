"""Ad-hoc, read-only search preview — hits Prowlarr and shows what the parser/matcher
would see, without grabbing anything or touching the database. Useful for checking
indexer coverage and parsing quality for a title before adding it as a Publication.
"""

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from kioskarr.config import settings
from kioskarr.jobs.search_job import search_with_fallback
from kioskarr.parser import parse
from kioskarr.prowlarr_client import ProwlarrClient

router = APIRouter(prefix="/search", tags=["search"])


class ParsedOut(BaseModel):
    title_guess: str
    identifier: str | None
    identifier_kind: str | None
    format_ext: str | None


class ReleasePreview(BaseModel):
    title: str
    indexer_name: str | None
    seeders: int | None
    size: int | None
    parsed: ParsedOut


@router.get("/preview", response_model=list[ReleasePreview])
def preview_search(query: str) -> list[ReleasePreview]:
    try:
        settings.require_prowlarr()
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc

    prowlarr = ProwlarrClient(settings.prowlarr_url, settings.prowlarr_api_key)
    try:
        releases = search_with_fallback(prowlarr, query)
    except requests.RequestException as exc:
        raise HTTPException(502, f"Prowlarr search failed: {exc}") from exc

    previews = []
    for release in releases:
        parsed = parse(release.title)
        previews.append(
            ReleasePreview(
                title=release.title,
                indexer_name=release.indexer_name,
                seeders=release.seeders,
                size=release.size,
                parsed=ParsedOut(
                    title_guess=parsed.title_guess,
                    identifier=parsed.identifier,
                    identifier_kind=parsed.identifier_kind,
                    format_ext=parsed.format_ext,
                ),
            )
        )
    return previews

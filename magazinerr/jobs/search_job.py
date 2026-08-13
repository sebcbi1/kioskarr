"""Periodic search: for each monitored Publication, search indexers via Prowlarr,
parse+match candidates, and grab anything new that isn't already owned or in flight.

No canonical release calendar exists for this content type, so this is reactive:
search now, decide from what comes back — see plan's Context section.
"""

import logging

from sqlalchemy.orm import Session

from magazinerr.config import settings
from magazinerr.matcher import is_confident_match, issue_already_owned
from magazinerr.models import Grab, GrabStatus, Publication
from magazinerr.parser import parse
from magazinerr.prowlarr_client import ProwlarrClient
from magazinerr.qbittorrent_client import QBittorrentClient

logger = logging.getLogger(__name__)

BOOKS_MAGAZINES_CATEGORY = 7010


def search_with_fallback(prowlarr: ProwlarrClient, query: str) -> list:
    releases = prowlarr.search(query, categories=[BOOKS_MAGAZINES_CATEGORY])
    if releases:
        return releases
    # Not every indexer populates 7010 consistently — fall back to an
    # unfiltered title search rather than assuming the category is reliable.
    return prowlarr.search(query)


def _already_in_flight(db: Session, publication_id: int, identifier: str) -> bool:
    return (
        db.query(Grab)
        .filter(
            Grab.publication_id == publication_id,
            Grab.identifier == identifier,
            Grab.status.in_([GrabStatus.downloading, GrabStatus.completed]),
        )
        .first()
        is not None
    )


def run_search_job(
    db: Session,
    prowlarr: ProwlarrClient,
    qbt: QBittorrentClient,
    publications: list[Publication] | None = None,
) -> list[Grab]:
    targets = publications if publications is not None else (
        db.query(Publication).filter(Publication.monitored.is_(True)).all()
    )
    new_grabs: list[Grab] = []

    for publication in targets:
        seen_guids: set[str] = set()
        candidates = []
        for term in publication.all_search_terms():
            for release in search_with_fallback(prowlarr, term):
                if release.guid and release.guid in seen_guids:
                    continue
                if release.guid:
                    seen_guids.add(release.guid)
                candidates.append(release)

        for release in candidates:
            parsed = parse(release.title)
            if parsed.identifier is None:
                continue  # can't safely dedupe without an identifier
            if not is_confident_match(parsed, publication.title, publication.aliases):
                continue
            if issue_already_owned(db, publication.id, parsed.identifier):
                continue
            if _already_in_flight(db, publication.id, parsed.identifier):
                continue
            min_seeders = publication.min_seeders or settings.default_min_seeders
            if (release.seeders or 0) < min_seeders:
                continue

            try:
                qbt.add_torrent(release.download_url, category=settings.qbittorrent_category)
            except Exception:
                logger.exception(
                    "Failed to add torrent for %s: %s", publication.title, release.title
                )
                continue

            grab = Grab(
                publication_id=publication.id,
                release_title=release.title,
                release_guid=release.guid,
                identifier=parsed.identifier,
                indexer_id=str(release.indexer_id) if release.indexer_id is not None else None,
                status=GrabStatus.downloading,
            )
            db.add(grab)
            new_grabs.append(grab)

        db.commit()

    return new_grabs

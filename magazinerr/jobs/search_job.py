"""Periodic search: for each monitored Publication, search indexers via Prowlarr,
parse+match candidates, and grab anything new that isn't already owned or in flight.

No canonical release calendar exists for this content type, so this is reactive:
search now, decide from what comes back — see plan's Context section.

Cold start (a publication's very first search) is a special case: an indexer can
return the entire back-catalog it happens to have (we've seen 80+ historical
issues for a single title), and every one of them looks "new" since nothing is
owned yet. Instead of grabbing all of it, only the `grab_last_n` most recent
eligible candidates are grabbed, and a `baseline_identifier` floor is recorded so
nothing at or below it is ever reconsidered — including on later cycles, which
also protects against an old issue resurfacing (e.g. a reseed) in search results.
"""

import logging

from sqlalchemy.orm import Session

from magazinerr.config import settings
from magazinerr.matcher import is_confident_match, issue_already_owned
from magazinerr.models import Grab, GrabStatus, Publication
from magazinerr.parser import ParsedRelease, identifier_sort_key, is_identifier_newer, parse
from magazinerr.prowlarr_client import ProwlarrClient, Release
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


def _eligible_candidates(
    db: Session, publication: Publication, candidates: list[Release]
) -> list[tuple[Release, ParsedRelease]]:
    eligible = []
    min_seeders = publication.min_seeders or settings.default_min_seeders
    for release in candidates:
        parsed = parse(release.title)
        if parsed.identifier is None:
            continue  # can't safely dedupe (or order) without an identifier
        if not is_confident_match(parsed, publication.title, publication.aliases):
            continue
        if issue_already_owned(db, publication.id, parsed.identifier):
            continue
        if _already_in_flight(db, publication.id, parsed.identifier):
            continue
        if (release.seeders or 0) < min_seeders:
            continue
        if not is_identifier_newer(parsed.identifier, publication.baseline_identifier):
            continue
        eligible.append((release, parsed))
    return eligible


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

        eligible = _eligible_candidates(db, publication, candidates)

        if publication.baseline_identifier is None:
            # Cold start: take only the N most recent, then set a permanent
            # floor so the rest of the back-catalog is never reconsidered.
            eligible.sort(key=lambda pair: identifier_sort_key(pair[1].identifier), reverse=True)
            to_grab = eligible[: publication.grab_last_n]
            if to_grab:
                publication.baseline_identifier = to_grab[-1][1].identifier
        else:
            to_grab = eligible

        for release, parsed in to_grab:
            try:
                torrent_hash = qbt.add_torrent(release.download_url, category=settings.qbittorrent_category)
            except Exception:
                logger.exception(
                    "Failed to add torrent for %s: %s", publication.title, release.title
                )
                continue
            if torrent_hash is None:
                logger.warning(
                    "Added %s for %s but no new torrent hash appeared — likely a duplicate "
                    "of a torrent qBittorrent already had; it won't be tracked for import.",
                    release.title,
                    publication.title,
                )

            grab = Grab(
                publication_id=publication.id,
                release_title=release.title,
                release_guid=release.guid,
                identifier=parsed.identifier,
                torrent_hash=torrent_hash,
                indexer_id=str(release.indexer_id) if release.indexer_id is not None else None,
                status=GrabStatus.downloading,
            )
            db.add(grab)
            new_grabs.append(grab)

        db.commit()

    return new_grabs

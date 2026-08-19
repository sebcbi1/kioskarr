"""Fuzzy title matching + identifier dedupe against already-owned Issues.

No canonical metadata source exists to look up "does this release belong to this
publication" — instead we fuzzy-match the parsed title guess against the
publication's title + user-supplied aliases (mirrors Mylar3's altname pattern).
"""

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from kioskarr.parser import ParsedRelease, normalize

DEFAULT_MATCH_CONFIDENCE_THRESHOLD = 75.0


def title_match_score(parsed: ParsedRelease, publication_title: str, aliases: list[str]) -> float:
    # parsed.title_guess already went through normalize() (dash/diacritic/etc.
    # stripping) when the release title was parsed — the publication's own
    # title/aliases need the same treatment before comparing, or a name with
    # real punctuation (e.g. "Ouest-France", the actual official name) scores
    # lower than it should purely from an inconsistent left/right-hand format.
    best = 0.0
    for candidate in (publication_title, *aliases):
        score = fuzz.token_sort_ratio(parsed.title_guess.lower(), normalize(candidate).lower())
        best = max(best, score)
    return best


def is_confident_match(
    parsed: ParsedRelease,
    publication_title: str,
    aliases: list[str],
    threshold: float = DEFAULT_MATCH_CONFIDENCE_THRESHOLD,
) -> bool:
    return title_match_score(parsed, publication_title, aliases) >= threshold


def issue_already_owned(db_session: Session, publication_id: int, identifier: str) -> bool:
    from kioskarr.models import Issue

    return (
        db_session.query(Issue)
        .filter(Issue.publication_id == publication_id, Issue.identifier == identifier)
        .first()
        is not None
    )


def is_already_in_flight(db_session: Session, publication_id: int, identifier: str) -> bool:
    # needs_review counts as "already handled" too — otherwise a stuck grab
    # (e.g. the duplicate-torrent case) would get re-attempted and re-flagged
    # every single search cycle instead of waiting for manual resolution.
    from kioskarr.models import Grab, GrabStatus

    return (
        db_session.query(Grab)
        .filter(
            Grab.publication_id == publication_id,
            Grab.identifier == identifier,
            Grab.status.in_([GrabStatus.downloading, GrabStatus.completed, GrabStatus.needs_review]),
        )
        .first()
        is not None
    )

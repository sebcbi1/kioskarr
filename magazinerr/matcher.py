"""Fuzzy title matching + identifier dedupe against already-owned Issues.

No canonical metadata source exists to look up "does this release belong to this
publication" — instead we fuzzy-match the parsed title guess against the
publication's title + user-supplied aliases (mirrors Mylar3's altname pattern).
"""

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from magazinerr.config import settings
from magazinerr.parser import ParsedRelease


def title_match_score(parsed: ParsedRelease, publication_title: str, aliases: list[str]) -> float:
    best = 0.0
    for candidate in (publication_title, *aliases):
        score = fuzz.token_sort_ratio(parsed.title_guess.lower(), candidate.lower())
        best = max(best, score)
    return best


def is_confident_match(
    parsed: ParsedRelease,
    publication_title: str,
    aliases: list[str],
    threshold: float | None = None,
) -> bool:
    score = title_match_score(parsed, publication_title, aliases)
    return score >= (threshold if threshold is not None else settings.match_confidence_threshold)


def issue_already_owned(db_session: Session, publication_id: int, identifier: str) -> bool:
    from magazinerr.models import Issue

    return (
        db_session.query(Issue)
        .filter(Issue.publication_id == publication_id, Issue.identifier == identifier)
        .first()
        is not None
    )

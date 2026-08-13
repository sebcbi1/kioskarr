import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from magazinerr.db import Base
from magazinerr.matcher import is_confident_match, issue_already_owned, title_match_score
from magazinerr.models import Issue, Publication, PublicationType
from magazinerr.parser import parse


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_title_match_score_high_for_close_title():
    parsed = parse("Wired USA - August 2026.pdf")
    score = title_match_score(parsed, "Wired USA", [])
    assert score > 90


def test_title_match_score_uses_best_alias():
    parsed = parse("Nat Geo - August 2026.pdf")
    # doesn't match canonical title well, but matches an alias
    score_without_alias = title_match_score(parsed, "National Geographic", [])
    score_with_alias = title_match_score(parsed, "National Geographic", ["Nat Geo"])
    assert score_with_alias > score_without_alias


def test_is_confident_match_respects_threshold():
    parsed = parse("Totally Unrelated Zine - August 2026.pdf")
    assert not is_confident_match(parsed, "Wired USA", [], threshold=75.0)


def test_issue_already_owned(db_session):
    pub = Publication(
        title="Wired USA",
        type=PublicationType.magazine,
        aliases=[],
        target_dir="/library/wired",
    )
    db_session.add(pub)
    db_session.commit()

    assert issue_already_owned(db_session, pub.id, "2026-08") is False

    db_session.add(
        Issue(
            publication_id=pub.id,
            identifier="2026-08",
            file_path="/library/wired/2026-08.pdf",
            source_release_title="Wired USA - August 2026.pdf",
        )
    )
    db_session.commit()

    assert issue_already_owned(db_session, pub.id, "2026-08") is True
    assert issue_already_owned(db_session, pub.id, "2026-09") is False

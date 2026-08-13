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


def test_confidently_matches_real_daily_but_rejects_differently_branded_siblings():
    # Real live-data check: "Le Monde" (the daily) must confidently match, while
    # its differently-branded siblings (which also contain "Le Monde" as a
    # substring) must not — otherwise a "Le Monde" publication would also grab
    # an unrelated magazine.
    daily = parse("Le.Monde.N.25342.Du.23.Juin.2026.FR.[PDF]-G11")
    assert is_confident_match(daily, "Le Monde", [])

    for sibling_title in [
        "Le.Monde.Diplomatique.N.865.Avril.2026.FR.[PDF]-G11",
        "Le.Monde.Du.Camping.Car.N374.Aout.Septembre.2025.FR.[PDF]-NOTAG",
        "Le.Monde.Magazine.Du.7.Mars.2026.FR.[PDF]-G11",
    ]:
        assert not is_confident_match(parse(sibling_title), "Le Monde", [])


def test_alias_with_punctuation_scores_the_same_as_its_normalized_form():
    # Real bug: title_match_score normalized the parsed release side but not
    # the publication title/alias side, so "Ouest-France" (the real official
    # name, with a dash) scored lower than "Ouest France" purely from that
    # inconsistency — 73.7 vs 94.7, straddling the default 75 threshold.
    parsed = parse("Ouest-France Edition France du 12.08.2025.pdf")
    dashed = title_match_score(parsed, "Ouest France", ["Ouest-France Edition France"])
    spaced = title_match_score(parsed, "Ouest France", ["Ouest France Edition France"])
    assert dashed == spaced


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

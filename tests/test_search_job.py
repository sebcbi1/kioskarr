import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from magazinerr.db import Base
from magazinerr.jobs.search_job import run_search_job
from magazinerr.models import Publication, PublicationType
from magazinerr.prowlarr_client import Release


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


class FakeProwlarr:
    """Returns the same release list for every query, regardless of category —
    good enough to exercise run_search_job's matching/dedup/baseline logic
    without a live Prowlarr instance."""

    def __init__(self, releases):
        self.releases = releases

    def search(self, query, categories=None, indexer_ids=None):
        return self.releases


class FakeQbt:
    def __init__(self):
        self.added = []

    def add_torrent(self, url, category):
        self.added.append((url, category))


def _release(title, guid, seeders=10):
    return Release(
        title=title,
        guid=guid,
        download_url=f"http://example/{guid}",
        indexer_id=1,
        indexer_name="TestIndexer",
        seeders=seeders,
        size=1000,
        protocol="torrent",
    )


def _publication(db_session, **kwargs):
    pub = Publication(
        title="Ouest France",
        type=PublicationType.newspaper,
        aliases=[],
        target_dir="/library/ouest-france",
        **kwargs,
    )
    db_session.add(pub)
    db_session.commit()
    return pub


# A cold-start batch shaped like the real "ouest france" search that motivated
# this feature: without a floor, all of these would be grabbed on day one.
OUEST_FRANCE_RELEASES = [
    _release("Ouest.France.Du.20.Juin.2026.FR.[PDF]-G11", "guid-20"),
    _release("Ouest.France.Du.21.Juin.2026.FR.[PDF]-G11", "guid-21"),
    _release("Ouest.France.Du.22.Juin.2026.FR.[PDF]-G11", "guid-22"),
]


def test_cold_start_grabs_only_latest_by_default(db_session):
    pub = _publication(db_session)  # grab_last_n defaults to 1
    prowlarr = FakeProwlarr(OUEST_FRANCE_RELEASES)
    qbt = FakeQbt()

    grabs = run_search_job(db_session, prowlarr, qbt, publications=[pub])

    assert len(grabs) == 1
    assert grabs[0].identifier == "2026-06-22"
    assert pub.baseline_identifier == "2026-06-22"
    assert len(qbt.added) == 1


def test_cold_start_grabs_last_n(db_session):
    pub = _publication(db_session, grab_last_n=2)
    prowlarr = FakeProwlarr(OUEST_FRANCE_RELEASES)
    qbt = FakeQbt()

    grabs = run_search_job(db_session, prowlarr, qbt, publications=[pub])

    assert sorted(g.identifier for g in grabs) == ["2026-06-21", "2026-06-22"]
    # baseline is the oldest of what was grabbed — the floor below which nothing counts
    assert pub.baseline_identifier == "2026-06-21"


def test_after_cold_start_only_newer_issues_are_grabbed(db_session):
    pub = _publication(db_session)
    prowlarr = FakeProwlarr(OUEST_FRANCE_RELEASES)
    qbt = FakeQbt()
    run_search_job(db_session, prowlarr, qbt, publications=[pub])
    assert pub.baseline_identifier == "2026-06-22"

    # Next cycle: a genuinely new issue appears, alongside the old back-catalog
    # (e.g. a reseed bumping an old release back into search results) — only
    # the new one should be grabbed.
    new_release = _release("Ouest.France.Du.23.Juin.2026.FR.[PDF]-G11", "guid-23")
    prowlarr.releases = [*OUEST_FRANCE_RELEASES, new_release]

    grabs = run_search_job(db_session, prowlarr, qbt, publications=[pub])

    assert len(grabs) == 1
    assert grabs[0].identifier == "2026-06-23"


def test_cold_start_with_no_eligible_candidates_leaves_baseline_unset(db_session):
    pub = _publication(db_session, min_seeders=100)  # nothing meets this threshold
    prowlarr = FakeProwlarr(OUEST_FRANCE_RELEASES)
    qbt = FakeQbt()

    grabs = run_search_job(db_session, prowlarr, qbt, publications=[pub])

    assert grabs == []
    assert pub.baseline_identifier is None  # retries cold start next cycle

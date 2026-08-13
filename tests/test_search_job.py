import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from magazinerr.db import Base
from magazinerr.jobs.search_job import run_search_job
from magazinerr.models import Grab, GrabStatus, Publication, PublicationType, ReviewItem
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
    def __init__(self, simulate_duplicate=False, files_by_hash=None):
        self.added = []
        # A duplicate of a torrent qBittorrent already had: add "succeeds" but
        # no new hash ever appears, same as the real add_torrent's contract.
        self.simulate_duplicate = simulate_duplicate
        self.files_by_hash = files_by_hash or {}
        self.set_priorities_calls = []

    def add_torrent(self, url, category):
        self.added.append((url, category))
        if self.simulate_duplicate:
            return None
        return f"hash-{len(self.added)}"

    def get_files(self, torrent_hash):
        # Default: a single dummy file — len(files) <= 1 means the caller
        # never attempts a restriction, so tests that don't care about this
        # are unaffected.
        return self.files_by_hash.get(torrent_hash, [{"index": 0, "name": "single.pdf", "size": 1000}])

    def set_file_priorities(self, torrent_hash, file_indices, priority):
        self.set_priorities_calls.append((torrent_hash, file_indices, priority))


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
    kwargs.setdefault("aliases", [])
    pub = Publication(
        title="Ouest France",
        type=PublicationType.newspaper,
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
    assert grabs[0].torrent_hash == "hash-1"  # captured so import can find it later —
    # a torrent's *name* as later reported by qBittorrent isn't reliably the release
    # title we searched with, so matching by name alone isn't a safe fallback.
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


def test_duplicate_grab_gets_flagged_for_review_not_left_stuck(db_session):
    # Real bug found live-testing: a torrent qBittorrent already had elsewhere
    # never gets a new hash, so it could never be matched back to by import_job
    # and would sit at "downloading" forever with no way to resolve it.
    pub = _publication(db_session)
    prowlarr = FakeProwlarr(OUEST_FRANCE_RELEASES)
    qbt = FakeQbt(simulate_duplicate=True)

    grabs = run_search_job(db_session, prowlarr, qbt, publications=[pub])

    assert len(grabs) == 1
    grab = grabs[0]
    assert grab.torrent_hash is None
    assert grab.status == GrabStatus.needs_review

    review_item = db_session.query(ReviewItem).filter(ReviewItem.grab_id == grab.id).one()
    assert not review_item.resolved
    assert "duplicate" in review_item.reason.lower()

    # baseline still advances — the cold-start floor shouldn't get stuck
    # re-litigating the same duplicate on every future cycle either.
    assert pub.baseline_identifier == "2026-06-22"


def test_duplicate_grab_is_not_retried_on_the_next_cycle(db_session):
    pub = _publication(db_session)
    prowlarr = FakeProwlarr(OUEST_FRANCE_RELEASES)
    qbt = FakeQbt(simulate_duplicate=True)
    run_search_job(db_session, prowlarr, qbt, publications=[pub])
    assert db_session.query(Grab).count() == 1

    # Same candidates come back next cycle — should not create a second Grab
    # (and re-flag) for the same already-flagged identifier.
    grabs = run_search_job(db_session, prowlarr, qbt, publications=[pub])

    assert grabs == []
    assert db_session.query(Grab).count() == 1


def test_restricts_download_when_torrent_bundles_an_extra_file(db_session):
    # Real release shape confirmed live: a torrent can bundle more than the
    # one file we want (a supplement, or in the extreme case a "national
    # newspapers" bundle with a dozen different publications for one date).
    # Rather than downloading everything and sorting it out at import time,
    # skip the file(s) we don't want as soon as we know which is ours.
    pub = _publication(db_session, aliases=["Ouest France Edition France"])
    release = _release("Ouest.France.Du.22.Juin.2026.FR.[PDF]-G11", "guid-bundle")
    prowlarr = FakeProwlarr([release])
    bundle_files = [
        {"index": 0, "name": "Ouest-France Edition France du 22.06.2026.pdf", "size": 12_000_000},
        {"index": 1, "name": "TV Mag Supplement du 22.06.2026.pdf", "size": 5_000_000},
    ]
    qbt = FakeQbt(files_by_hash={"hash-1": bundle_files})

    grabs = run_search_job(db_session, prowlarr, qbt, publications=[pub])

    assert len(grabs) == 1
    assert qbt.set_priorities_calls == [("hash-1", [1], 0)]  # skip everything but index 0


def test_does_not_restrict_when_ambiguous(db_session):
    # If we can't tell which file is ours (or several distinct issues match),
    # leave everything downloading so import-time review has full access —
    # restricting here could throw away a file the user actually needs.
    pub = _publication(db_session)
    release = _release("Ouest.France.Du.22.Juin.2026.FR.[PDF]-G11", "guid-bundle")
    prowlarr = FakeProwlarr([release])
    unmatched_files = [
        {"index": 0, "name": "Le Figaro du 22.06.2026.pdf", "size": 30_000_000},
        {"index": 1, "name": "Les Echos du 22.06.2026.pdf", "size": 20_000_000},
    ]
    qbt = FakeQbt(files_by_hash={"hash-1": unmatched_files})

    grabs = run_search_job(db_session, prowlarr, qbt, publications=[pub])

    assert len(grabs) == 1
    assert qbt.set_priorities_calls == []

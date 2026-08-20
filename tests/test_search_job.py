import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kioskarr.app_settings import ensure_app_settings_seeded, get_app_settings
from kioskarr.db import Base
from kioskarr.jobs.search_job import grab_release_candidate, run_search_job
from kioskarr.models import Grab, GrabStatus, Publication, PublicationType, ReviewItem
from kioskarr.parser import parse
from kioskarr.prowlarr_client import Release


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ensure_app_settings_seeded(session)
        yield session


class FakeProwlarr:
    """Returns the same release list for every query, regardless of category —
    good enough to exercise run_search_job's matching/dedup/baseline logic
    without a live Prowlarr instance."""

    def __init__(self, releases, indexer_priorities=None):
        self.releases = releases
        self.indexer_priorities = indexer_priorities or {}

    def search(self, query, categories=None, indexer_ids=None):
        return self.releases

    def get_indexer_priorities(self):
        return self.indexer_priorities


class FakeQbt:
    def __init__(self, simulate_duplicate=False, files_by_hash=None, existing_hashes=None):
        self.added = []
        # A duplicate of a torrent qBittorrent already had: add "succeeds" but
        # no new hash ever appears, same as the real add_torrent's contract.
        self.simulate_duplicate = simulate_duplicate
        self.files_by_hash = files_by_hash or {}
        self.set_priorities_calls = []
        # Torrents already present (any category) before this run — used to
        # detect a known-duplicate release by info_hash upfront.
        self.existing_hashes = set(existing_hashes or [])

    def add_torrent(self, url, category):
        self.added.append((url, category))
        if self.simulate_duplicate:
            return None
        return f"hash-{len(self.added)}"

    def list_torrents(self, category=None):
        return [{"hash": h} for h in self.existing_hashes]

    def get_files(self, torrent_hash):
        # Default: a single dummy file — len(files) <= 1 means the caller
        # never attempts a restriction, so tests that don't care about this
        # are unaffected.
        return self.files_by_hash.get(torrent_hash, [{"index": 0, "name": "single.pdf", "size": 1000}])

    def set_file_priorities(self, torrent_hash, file_indices, priority):
        self.set_priorities_calls.append((torrent_hash, file_indices, priority))


def _release(title, guid, seeders=10, indexer_id=1, info_hash=None):
    return Release(
        title=title,
        guid=guid,
        download_url=f"http://example/{guid}",
        indexer_id=indexer_id,
        indexer_name="TestIndexer",
        seeders=seeders,
        size=1000,
        protocol="torrent",
        info_hash=info_hash,
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


def test_picks_higher_priority_indexer_when_same_issue_found_on_two_indexers(db_session):
    # Prowlarr aggregates multiple indexers, so the same issue can come back
    # as two distinct candidates (e.g. both C411 and TR4KER have an upload of
    # the same date) — must grab exactly one, not both.
    pub = _publication(db_session)
    same_day = [
        _release("Ouest.France.Du.22.Juin.2026.FR.[PDF]-G11", "guid-low-priority", indexer_id=1, seeders=50),
        _release("Ouest.France.Du.22.Juin.2026.FR.[PDF]-G11", "guid-high-priority", indexer_id=2, seeders=5),
    ]
    prowlarr = FakeProwlarr(same_day, indexer_priorities={1: 25, 2: 10})  # lower number = preferred
    qbt = FakeQbt()

    grabs = run_search_job(db_session, prowlarr, qbt, publications=[pub])

    assert len(grabs) == 1
    assert grabs[0].release_guid == "guid-high-priority"  # indexer 2 has priority 10 < 25


def test_picks_more_seeders_when_indexer_priority_ties(db_session):
    pub = _publication(db_session)
    same_day = [
        _release("Ouest.France.Du.22.Juin.2026.FR.[PDF]-G11", "guid-few-seeders", indexer_id=1, seeders=2),
        _release("Ouest.France.Du.22.Juin.2026.FR.[PDF]-G11", "guid-many-seeders", indexer_id=1, seeders=50),
    ]
    prowlarr = FakeProwlarr(same_day, indexer_priorities={1: 25})
    qbt = FakeQbt()

    grabs = run_search_job(db_session, prowlarr, qbt, publications=[pub])

    assert len(grabs) == 1
    assert grabs[0].release_guid == "guid-many-seeders"


def test_known_duplicate_via_info_hash_flagged_without_calling_add_torrent(db_session):
    # Knowing the hash upfront (Prowlarr's own infoHash field) means a
    # duplicate can be detected before ever calling add_torrent, instead of
    # only finding out after the fact from a missing hash.
    pub = _publication(db_session)
    release = _release(
        "Ouest.France.Du.22.Juin.2026.FR.[PDF]-G11", "guid-22", info_hash="deadbeef1234"
    )
    prowlarr = FakeProwlarr([release])
    qbt = FakeQbt(existing_hashes={"deadbeef1234"})

    grabs = run_search_job(db_session, prowlarr, qbt, publications=[pub])

    assert len(grabs) == 1
    assert grabs[0].status == GrabStatus.needs_review
    assert grabs[0].torrent_hash is None
    assert qbt.added == []  # never even attempted — already known to be a duplicate


def test_unconfirmed_info_hash_does_not_produce_a_phantom_downloading_grab(db_session):
    # Real bug, confirmed live: release.info_hash is just Prowlarr's own
    # precomputed hash of the *expected* content — not proof qBittorrent
    # actually added it. If add_torrent's own poll never sees a new hash
    # appear (simulate_duplicate=True — qBittorrent's list never changes),
    # the grab must NOT be recorded as "downloading" just because Prowlarr
    # happened to supply an info_hash; that hash was never confirmed to
    # exist in qBittorrent at all.
    pub = _publication(db_session)
    release = _release(
        "Ouest.France.Du.22.Juin.2026.FR.[PDF]-G11", "guid-22", info_hash="unconfirmed-hash"
    )
    prowlarr = FakeProwlarr([release])
    qbt = FakeQbt(simulate_duplicate=True)  # add_torrent "succeeds" but no new hash ever appears

    grabs = run_search_job(db_session, prowlarr, qbt, publications=[pub])

    assert len(grabs) == 1
    assert grabs[0].status == GrabStatus.needs_review
    assert grabs[0].torrent_hash is None
    assert qbt.added == [(release.download_url, "kioskarr")]  # add_torrent WAS attempted this time


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


def test_grab_release_candidate_bypasses_all_gating(db_session):
    # grab_release_candidate is the mechanics the manual-grab API endpoint
    # calls directly, with none of _eligible_candidates's gating upstream of
    # it — a release that would never pass the automatic threshold/seeder
    # checks must still get grabbed when called this way.
    pub = _publication(db_session, min_seeders=999)
    release = _release("Totally Unrelated Zine - August 2026.pdf", "guid-x", seeders=0)
    parsed = parse(release.title)
    qbt = FakeQbt()
    app_settings = get_app_settings(db_session)

    grab = grab_release_candidate(db_session, qbt, pub, release, parsed, set(), app_settings)

    assert grab.status == GrabStatus.downloading
    assert qbt.added == [(release.download_url, "kioskarr")]

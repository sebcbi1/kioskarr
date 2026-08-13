import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from magazinerr.db import Base
from magazinerr.jobs.import_job import _classify_files, run_import_job
from magazinerr.models import FormatPreference, Grab, GrabStatus, Publication, PublicationType, ReviewItem


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


# Real file listing pulled directly from the actual "Le Monde Diplomatique 2013
# INTEGRALE" torrent (fetched via Prowlarr's download proxy and bencode-parsed
# by hand) — a genuine annual-archive release bundling 12 separate monthly
# issues plus an NFO in one torrent. The old _pick_main_file would have picked
# only the largest (March) and silently discarded the other 11.
REAL_ARCHIVE_FILES = [
    {"name": "LE MONDE DIPLOMATIQUE N°708 - Mars 2013.pdf", "size": 9871152},
    {"name": "LE MONDE DIPLOMATIQUE N°707 - Fevrier 2013.pdf", "size": 3921409},
    {"name": "LE MONDE DIPLOMATIQUE N°706-  Janvier 2013.pdf", "size": 6518322},
    {"name": "LE MONDE DIPLOMATIQUE N°709 - Avril 2013.pdf", "size": 7061119},
    {"name": "LE MONDE DIPLOMATIQUE N°710 - Mai 2013.pdf", "size": 7972984},
    {"name": "LE MONDE DIPLOMATIQUE N°711 - Juin 2013.pdf", "size": 3493067},
    {"name": "LE MONDE DIPLOMATIQUE N°712 - Juillet 2013.pdf", "size": 5266154},
    {"name": "LE MONDE DIPLOMATIQUE N°713 - Aout 2013.pdf", "size": 4631887},
    {"name": "LE MONDE DIPLOMATIQUE N°714 - Septembre 2013.pdf", "size": 5059638},
    {"name": "LE MONDE DIPLOMATIQUE N°715 - Octobre 2013.pdf", "size": 4499569},
    {"name": "LE MONDE DIPLOMATIQUE N°716 - Novembre 2013.pdf", "size": 4126725},
    {"name": "LE MONDE DIPLOMATIQUE N°717 - Décembre 2013.pdf", "size": 7466096},
    {"name": "Le.Monde.Diplomatique.2013.COMPLETE.VFF.[PDF]-NOTAG.nfo", "size": 1075},
]

# Real file listing from an actual normal single-issue torrent ("N.865").
REAL_SINGLE_ISSUE_FILES = [
    {"name": "Le Monde diplomatique N865 • Avril 2026.Pdf", "size": 14251844},
]


def test_classify_files_flags_real_multi_issue_archive():
    chosen, others = _classify_files(REAL_ARCHIVE_FILES, "pdf")
    assert chosen["name"] == "LE MONDE DIPLOMATIQUE N°708 - Mars 2013.pdf"
    assert len(others) == 11  # every other month — the NFO correctly excluded as junk
    assert all("nfo" not in f["name"].lower() for f in others)


def test_classify_files_no_false_positive_on_real_single_issue():
    chosen, others = _classify_files(REAL_SINGLE_ISSUE_FILES, "pdf")
    assert chosen["name"] == REAL_SINGLE_ISSUE_FILES[0]["name"]
    assert others == []


def test_classify_files_ignores_small_junk_files():
    files = [
        {"name": "issue.pdf", "size": 20_000_000},
        {"name": "cover.jpg", "size": 200_000},
        {"name": "info.nfo", "size": 800},
    ]
    chosen, others = _classify_files(files, "any")
    assert chosen["name"] == "issue.pdf"
    assert others == []


def test_classify_files_respects_format_preference():
    files = [
        {"name": "issue.epub", "size": 5_000_000},
        {"name": "issue.pdf", "size": 20_000_000},
    ]
    chosen, others = _classify_files(files, "epub")
    assert chosen["name"] == "issue.epub"
    assert others == []


class FakeQbt:
    def __init__(self, torrents, files_by_hash):
        self.torrents = torrents
        self.files_by_hash = files_by_hash

    def list_torrents(self, category=None):
        return self.torrents

    def get_files(self, torrent_hash):
        return self.files_by_hash[torrent_hash]


def test_run_import_job_flags_multi_issue_archive_for_review_instead_of_importing_one(db_session):
    pub = Publication(
        title="Le Monde Diplomatique",
        type=PublicationType.magazine,
        aliases=[],
        format_preference=FormatPreference.pdf,
        target_dir="/library/lmd",
    )
    db_session.add(pub)
    db_session.commit()

    grab = Grab(
        publication_id=pub.id,
        release_title="Le.Monde.Diplomatique.2013.[INTEGRALE].FR.[PDF]-NOTAG",
        release_guid="guid-1",
        identifier="2013-03",
        torrent_hash="abc123",
        status=GrabStatus.downloading,
    )
    db_session.add(grab)
    db_session.commit()

    torrent = {
        "hash": "abc123",
        "name": "Le.Monde.Diplomatique.2013.[INTEGRALE].FR.[PDF]-NOTAG",
        "progress": 1,
        "save_path": "/downloads",
        "content_path": "/downloads/lmd-2013",
    }
    qbt = FakeQbt(torrents=[torrent], files_by_hash={"abc123": REAL_ARCHIVE_FILES})

    result = run_import_job(db_session, qbt)

    assert result["imported"] == []
    assert result["flagged_for_review"] == [grab.id]

    db_session.refresh(grab)
    assert grab.status == GrabStatus.needs_review

    review_item = db_session.query(ReviewItem).filter(ReviewItem.grab_id == grab.id).one()
    assert "multiple" in review_item.reason.lower()

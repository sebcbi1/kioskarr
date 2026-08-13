import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from magazinerr.db import Base
from magazinerr.jobs.import_job import _select_issue_file, run_import_job
from magazinerr.models import FormatPreference, Grab, GrabStatus, Publication, PublicationType, ReviewItem


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


PUB_TITLE = "Le Monde Diplomatique"

# Real file listing pulled directly from the actual "Le Monde Diplomatique 2013
# INTEGRALE" torrent (fetched via Prowlarr's download proxy and bencode-parsed
# by hand) — a genuine annual-archive release bundling 12 separate monthly
# issues plus an NFO in one torrent. Picking by size alone would have picked
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


def test_flags_real_multi_issue_archive_by_content_not_size():
    chosen, others = _select_issue_file(REAL_ARCHIVE_FILES, "any", PUB_TITLE, [])
    assert chosen["name"] == "LE MONDE DIPLOMATIQUE N°708 - Mars 2013.pdf"
    assert len(others) == 11  # every other month — the NFO correctly excluded, not just by size
    assert all("nfo" not in f["name"].lower() for f in others)


def test_no_false_positive_on_real_single_issue():
    chosen, others = _select_issue_file(REAL_SINGLE_ISSUE_FILES, "pdf", PUB_TITLE, [])
    assert chosen["name"] == REAL_SINGLE_ISSUE_FILES[0]["name"]
    assert others == []


def test_ignores_junk_files_by_extension_even_when_large():
    # A size-only heuristic would have flagged this as ambiguous (cover.jpg is
    # bigger than the 10%-of-largest threshold) — extension filtering means a
    # non-magazine file type is never even a candidate, regardless of size.
    files = [
        {"name": "Wired USA - August 2026.pdf", "size": 20_000_000},
        {"name": "cover.jpg", "size": 15_000_000},
        {"name": "info.nfo", "size": 800},
    ]
    chosen, others = _select_issue_file(files, "any", "Wired USA", [])
    assert chosen["name"] == "Wired USA - August 2026.pdf"
    assert others == []


def test_respects_format_preference():
    files = [
        {"name": "Wired USA - August 2026.epub", "size": 5_000_000},
        {"name": "Wired USA - August 2026.pdf", "size": 20_000_000},
    ]
    chosen, others = _select_issue_file(files, "epub", "Wired USA", [])
    assert chosen["name"] == "Wired USA - August 2026.epub"
    assert others == []


def test_same_issue_in_two_formats_is_not_treated_as_ambiguous():
    # Same identifier either way — no information would be lost by picking
    # one, so this shouldn't be flagged the way genuinely distinct issues are.
    files = [
        {"name": "Wired USA - August 2026.epub", "size": 5_000_000},
        {"name": "Wired USA - August 2026.pdf", "size": 20_000_000},
    ]
    chosen, others = _select_issue_file(files, "any", "Wired USA", [])
    assert chosen["name"] == "Wired USA - August 2026.pdf"  # larger of the two
    assert others == []


def test_uses_the_sole_typed_file_even_if_it_doesnt_confidently_match():
    # Only one real candidate exists — no ambiguity to guess wrong about, so
    # use it. The caller's own confidence check downstream still catches a bad
    # single-file match and flags it for review.
    files = [{"name": "scan001.pdf", "size": 20_000_000}]
    chosen, others = _select_issue_file(files, "any", "Wired USA", [])
    assert chosen["name"] == "scan001.pdf"
    assert others == []


def test_does_not_guess_by_size_when_several_candidates_none_confidently_match():
    # Real bug found live: with more than one candidate and nothing confident,
    # falling back to "the largest" has no relationship to which is ours — in
    # a bundle of different publications for one date, that risks importing a
    # wholly different publication mislabeled as this one.
    files = [
        {"name": "scan001.pdf", "size": 20_000_000},
        {"name": "scan002.pdf", "size": 500_000},
    ]
    chosen, others = _select_issue_file(files, "any", "Wired USA", [])
    assert chosen is None
    assert {f["name"] for f in others} == {"scan001.pdf", "scan002.pdf"}


def test_real_national_newspaper_bundle_isolates_the_right_publication():
    # Real torrent found live: "Journaux Nationaux" bundles a dozen different
    # French dailies for one date in a single torrent. Only the file whose
    # name confidently matches the publication should be picked — not the
    # largest file overall (which here is Le Figaro, unrelated to Ouest France).
    files = [
        {"name": "Aujourd'hui en France du 12.08.2025.pdf", "size": 11926616},
        {"name": "L'Equipe du 12.08.2025.pdf", "size": 27770279},
        {"name": "Le Figaro du 12.08.2025.pdf", "size": 31292638},  # largest of all
        {"name": "Les Echos du 12.08.2025.pdf", "size": 23221682},
        {"name": "Ouest-France Edition France du 12.08.2025.pdf", "size": 12574174},
    ]
    # "Ouest-France Edition France" is the real uploader naming — needs the
    # alias, the same way "Science & Vie" vs "Science et Vie" did; without it
    # this should correctly refuse to guess rather than pick Le Figaro.
    chosen, others = _select_issue_file(files, "any", "Ouest France", [])
    assert chosen is None
    assert len(others) == 5

    chosen, others = _select_issue_file(
        files, "any", "Ouest France", ["Ouest-France Edition France"]
    )
    assert chosen["name"] == "Ouest-France Edition France du 12.08.2025.pdf"
    assert others == []


def test_returns_none_when_no_recognized_file_types_present():
    files = [{"name": "cover.jpg", "size": 5_000_000}, {"name": "info.nfo", "size": 800}]
    chosen, others = _select_issue_file(files, "any", "Wired USA", [])
    assert chosen is None
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
        title=PUB_TITLE,
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

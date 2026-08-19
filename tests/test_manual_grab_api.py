import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    # Same pattern as tests/test_review.py/tests/test_opds.py — main.app (and its
    # DB) is a process-wide singleton shared across every test in this file.
    from kioskarr.api.main import app
    from kioskarr.app_settings import get_app_settings
    from kioskarr.db import SessionLocal
    from kioskarr.models import Grab, Issue, Publication, ReviewItem

    db = SessionLocal()
    try:
        settings = get_app_settings(db)
        settings.admin_password_hash = ""
        settings.prowlarr_api_key = "test-key"
        settings.qbittorrent_password = "test-pass"
        db.query(ReviewItem).delete()
        db.query(Issue).delete()
        db.query(Grab).delete()
        db.query(Publication).delete()
        db.commit()
    finally:
        db.close()

    return TestClient(app)


class FakeQbt:
    def __init__(self, simulate_duplicate=False):
        self.added = []
        self.simulate_duplicate = simulate_duplicate

    def login(self):
        pass

    def ensure_category(self, category, save_path=""):
        pass

    def add_torrent(self, url, category, save_path=None, poll_attempts=10):
        self.added.append((url, category))
        if self.simulate_duplicate:
            return None
        return f"hash-{len(self.added)}"

    def list_torrents(self, category=None):
        return []

    def get_files(self, torrent_hash):
        return [{"index": 0, "name": "single.pdf", "size": 1000}]

    def set_file_priorities(self, torrent_hash, file_indices, priority):
        pass


def _make_publication(title="Ouest France", target_dir="/tmp/kioskarr-test-library", **kwargs):
    from kioskarr.db import SessionLocal
    from kioskarr.models import Publication

    db = SessionLocal()
    try:
        pub = Publication(title=title, target_dir=target_dir, **kwargs)
        db.add(pub)
        db.commit()
        db.refresh(pub)
        return pub.id
    finally:
        db.close()


def _make_issue(publication_id, identifier, file_path="/tmp/kioskarr-test-library/issue.pdf"):
    from kioskarr.db import SessionLocal
    from kioskarr.models import Issue

    db = SessionLocal()
    try:
        db.add(
            Issue(
                publication_id=publication_id,
                identifier=identifier,
                file_path=file_path,
                source_release_title="Some.Release.Title",
            )
        )
        db.commit()
    finally:
        db.close()


def _payload(title, guid="g1", **kwargs):
    return {
        "title": title,
        "guid": guid,
        "download_url": f"http://example/{guid}",
        **kwargs,
    }


def test_grab_release_bypasses_confidence_and_seeder_gating(client, monkeypatch):
    pub_id = _make_publication(min_seeders=999)
    fake_qbt = FakeQbt()
    monkeypatch.setattr("kioskarr.api.publications.QBittorrentClient", lambda *a, **k: fake_qbt)

    response = client.post(
        f"/publications/{pub_id}/grab-release",
        json=_payload("Totally Unrelated Zine - August 2026.pdf", seeders=0),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "downloading"
    assert body["identifier"] == "2026-08"
    assert fake_qbt.added == [("http://example/g1", "kioskarr")]


def test_grab_release_annotates_but_does_not_block_already_owned(client, monkeypatch):
    pub_id = _make_publication()
    _make_issue(pub_id, "2026-06-22")
    fake_qbt = FakeQbt()
    monkeypatch.setattr("kioskarr.api.publications.QBittorrentClient", lambda *a, **k: fake_qbt)

    response = client.post(
        f"/publications/{pub_id}/grab-release",
        json=_payload("Ouest.France.Du.22.Juin.2026.FR.[PDF]-G11"),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["already_owned"] is True
    # Warn-but-allow: a new Grab is still created, never blocked.
    assert fake_qbt.added == [("http://example/g1", "kioskarr")]


def test_grab_release_annotates_already_in_flight(client, monkeypatch):
    from kioskarr.db import SessionLocal
    from kioskarr.models import Grab, GrabStatus

    pub_id = _make_publication()
    db = SessionLocal()
    try:
        db.add(
            Grab(
                publication_id=pub_id,
                release_title="Some Release",
                release_guid="existing-guid",
                identifier="2026-06-22",
                status=GrabStatus.downloading,
            )
        )
        db.commit()
    finally:
        db.close()
    fake_qbt = FakeQbt()
    monkeypatch.setattr("kioskarr.api.publications.QBittorrentClient", lambda *a, **k: fake_qbt)

    response = client.post(
        f"/publications/{pub_id}/grab-release",
        json=_payload("Ouest.France.Du.22.Juin.2026.FR.[PDF]-G11"),
    )

    assert response.json()["already_in_flight"] is True


def test_grab_release_duplicate_in_qbittorrent_creates_review_item(client, monkeypatch):
    from kioskarr.db import SessionLocal
    from kioskarr.models import ReviewItem

    pub_id = _make_publication()
    fake_qbt = FakeQbt(simulate_duplicate=True)
    monkeypatch.setattr("kioskarr.api.publications.QBittorrentClient", lambda *a, **k: fake_qbt)

    response = client.post(
        f"/publications/{pub_id}/grab-release",
        json=_payload("Ouest.France.Du.22.Juin.2026.FR.[PDF]-G11"),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "needs_review"
    assert body["torrent_hash"] is None

    db = SessionLocal()
    try:
        items = db.query(ReviewItem).filter(ReviewItem.grab_id == body["id"]).all()
        assert len(items) == 1
    finally:
        db.close()


def test_grab_release_404_for_unknown_publication(client):
    response = client.post("/publications/999999/grab-release", json=_payload("Some Title"))
    assert response.status_code == 404


def test_grab_release_400_for_unparseable_title(client, monkeypatch):
    pub_id = _make_publication()
    monkeypatch.setattr("kioskarr.api.publications.QBittorrentClient", lambda *a, **k: FakeQbt())

    response = client.post(f"/publications/{pub_id}/grab-release", json=_payload("Some Random Text"))

    assert response.status_code == 400

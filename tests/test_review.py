import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    # Same pattern as tests/test_auth.py and tests/test_opds.py — main.app (and its
    # DB) is a process-wide singleton shared across every test in this file.
    from kioskarr.api.main import app
    from kioskarr.app_settings import get_app_settings
    from kioskarr.db import SessionLocal
    from kioskarr.models import Grab, Issue, Publication, ReviewItem

    db = SessionLocal()
    try:
        get_app_settings(db).admin_password_hash = ""
        db.query(ReviewItem).delete()
        db.query(Issue).delete()
        db.query(Grab).delete()
        db.query(Publication).delete()
        db.commit()
    finally:
        db.close()

    return TestClient(app)


def _make_publication_and_grab(target_dir):
    from kioskarr.db import SessionLocal
    from kioskarr.models import Grab, GrabStatus, Publication

    db = SessionLocal()
    try:
        pub = Publication(title="Ouest France", target_dir=str(target_dir))
        db.add(pub)
        db.commit()
        db.refresh(pub)

        grab = Grab(
            publication_id=pub.id,
            release_title="Ouest.France.Du.17.08.2026.FR.[PDF]-NoTag",
            release_guid="guid-1",
            identifier="2026-08-17",
            status=GrabStatus.needs_review,
        )
        db.add(grab)
        db.commit()
        db.refresh(grab)
        return pub.id, grab.id
    finally:
        db.close()


def _make_review_item(grab_id, file_path, reason="duplicate torrent"):
    from kioskarr.db import SessionLocal
    from kioskarr.models import ReviewItem

    db = SessionLocal()
    try:
        item = ReviewItem(grab_id=grab_id, file_path=str(file_path), reason=reason)
        db.add(item)
        db.commit()
        db.refresh(item)
        return item.id
    finally:
        db.close()


def test_resolve_with_missing_file_path_returns_clean_400_not_500(client, tmp_path):
    pub_id, grab_id = _make_publication_and_grab(tmp_path / "library")
    review_id = _make_review_item(grab_id, tmp_path / "recorded-but-not-real.pdf")

    response = client.post(
        f"/review/{review_id}/resolve",
        json={
            "publication_id": pub_id,
            "identifier": "2026-08-17",
            "file_path": str(tmp_path / "somewhere-else-that-does-not-exist.pdf"),
        },
    )

    assert response.status_code == 400
    assert "not necessarily what qBittorrent's own UI shows" in response.json()["detail"]


def test_resolve_with_real_file_path_succeeds(client, tmp_path):
    real_file = tmp_path / "existing-duplicate.pdf"
    real_file.write_bytes(b"real duplicate torrent content")
    pub_id, grab_id = _make_publication_and_grab(tmp_path / "library")
    review_id = _make_review_item(grab_id, tmp_path / "recorded-but-not-real.pdf")

    response = client.post(
        f"/review/{review_id}/resolve",
        json={"publication_id": pub_id, "identifier": "2026-08-17", "file_path": str(real_file)},
    )

    assert response.status_code == 200
    assert response.json()["resolved"] is True


def test_resolve_unknown_review_item_404s(client):
    response = client.post(
        "/review/999999/resolve", json={"publication_id": 1, "identifier": "2026-08-17"}
    )
    assert response.status_code == 404


def test_resolve_unknown_publication_404s(client, tmp_path):
    _, grab_id = _make_publication_and_grab(tmp_path / "library")
    review_id = _make_review_item(grab_id, tmp_path / "whatever.pdf")

    response = client.post(
        f"/review/{review_id}/resolve", json={"publication_id": 999999, "identifier": "x"}
    )

    assert response.status_code == 404


def test_discard_marks_grab_failed_and_resolves_item(client, tmp_path):
    from kioskarr.db import SessionLocal
    from kioskarr.models import Grab, GrabStatus

    _, grab_id = _make_publication_and_grab(tmp_path / "library")
    review_id = _make_review_item(grab_id, tmp_path / "whatever.pdf")

    response = client.post(f"/review/{review_id}/discard")

    assert response.status_code == 200
    assert response.json()["resolved"] is True

    db = SessionLocal()
    try:
        grab = db.get(Grab, grab_id)
        assert grab.status == GrabStatus.failed
    finally:
        db.close()

    assert client.get("/review").json() == []


def test_discard_unknown_review_item_404s(client):
    response = client.post("/review/999999/discard")
    assert response.status_code == 404

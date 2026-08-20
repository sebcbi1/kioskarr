import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    # Same pattern as tests/test_review.py/tests/test_opds.py — main.app (and its
    # DB) is a process-wide singleton shared across every test in this file.
    from kioskarr.api.main import app
    from kioskarr.app_settings import get_app_settings
    from kioskarr.db import SessionLocal
    from kioskarr.models import Grab, Publication

    db = SessionLocal()
    try:
        get_app_settings(db).admin_password_hash = ""
        db.query(Grab).delete()
        db.query(Publication).delete()
        db.commit()
    finally:
        db.close()

    return TestClient(app)


def _make_publication_and_grab(target_dir, status="downloading"):
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
            status=GrabStatus(status),
        )
        db.add(grab)
        db.commit()
        db.refresh(grab)
        return pub.id, grab.id
    finally:
        db.close()


@pytest.mark.parametrize(
    "new_status", ["downloading", "completed", "imported", "needs_review", "failed"]
)
def test_update_grab_status_succeeds(client, tmp_path, new_status):
    _, grab_id = _make_publication_and_grab(tmp_path / "library")

    response = client.patch(f"/grabs/{grab_id}", json={"status": new_status})

    assert response.status_code == 200
    assert response.json()["status"] == new_status


def test_update_grab_status_rejects_invalid_value(client, tmp_path):
    _, grab_id = _make_publication_and_grab(tmp_path / "library")

    response = client.patch(f"/grabs/{grab_id}", json={"status": "not-a-real-status"})

    assert response.status_code == 422


def test_update_grab_status_unknown_grab_404s(client):
    response = client.patch("/grabs/999999", json={"status": "failed"})
    assert response.status_code == 404

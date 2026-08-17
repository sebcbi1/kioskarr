import pytest
from fastapi.testclient import TestClient

from kioskarr.auth import hash_password, verify_password


def test_hash_and_verify_roundtrip():
    stored = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", stored)


def test_verify_rejects_wrong_password():
    stored = hash_password("correct horse battery staple")
    assert not verify_password("wrong password", stored)


def test_verify_rejects_empty_stored_hash():
    assert not verify_password("anything", "")


def test_hash_is_salted_differently_each_time():
    first = hash_password("same password")
    second = hash_password("same password")
    assert first != second
    assert verify_password("same password", first)
    assert verify_password("same password", second)


@pytest.fixture
def client():
    # kioskarr.api.main runs init_db()/ensure_app_settings_seeded() at import time —
    # tests/conftest.py has already pointed KIOSKARR_DATABASE_URL at a throwaway
    # temp file before this import can happen, so this never touches the real DB.
    from kioskarr.api.main import app

    # main.app (and its AppSettings row) is a process-wide singleton shared across
    # every test in this file — reset the password directly via the DB (bypassing
    # auth entirely) before each test so tests are order-independent, regardless of
    # what an earlier test left behind.
    from kioskarr.app_settings import get_app_settings
    from kioskarr.db import SessionLocal

    db = SessionLocal()
    try:
        get_app_settings(db).admin_password_hash = ""
        db.commit()
    finally:
        db.close()

    # Not using `with TestClient(app) as client` on purpose — that would run the
    # lifespan and start the real background scheduler, which is unnecessary noise
    # for these tests (auth doesn't depend on it).
    return TestClient(app)


def test_protected_route_open_when_no_password_set(client):
    response = client.get("/publications")
    assert response.status_code == 200


def test_protected_route_requires_login_once_password_set(client):
    client.patch("/settings", json={"admin_password": "hunter2"})

    response = client.get("/publications")

    assert response.status_code == 401


def test_login_then_protected_route_succeeds(client):
    client.patch("/settings", json={"admin_username": "admin", "admin_password": "hunter2"})

    login_response = client.post("/auth/login", json={"username": "admin", "password": "hunter2"})
    assert login_response.status_code == 200

    response = client.get("/publications")
    assert response.status_code == 200


def test_login_with_wrong_password_fails(client):
    client.patch("/settings", json={"admin_password": "hunter2"})

    response = client.post("/auth/login", json={"username": "admin", "password": "wrong"})

    assert response.status_code == 401


def test_logout_revokes_session(client):
    client.patch("/settings", json={"admin_password": "hunter2"})
    client.post("/auth/login", json={"username": "admin", "password": "hunter2"})
    assert client.get("/publications").status_code == 200

    client.post("/auth/logout")

    assert client.get("/publications").status_code == 401


def test_settings_response_never_exposes_secrets(client):
    client.patch(
        "/settings",
        json={"prowlarr_api_key": "super-secret-key", "qbittorrent_password": "super-secret-pw"},
    )

    body = client.get("/settings").json()

    assert "prowlarr_api_key" not in body
    assert "qbittorrent_password" not in body
    assert "admin_password" not in body
    assert "admin_password_hash" not in body
    assert body["prowlarr_api_key_set"] is True
    assert body["qbittorrent_password_set"] is True

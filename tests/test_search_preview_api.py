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
        db.query(ReviewItem).delete()
        db.query(Issue).delete()
        db.query(Grab).delete()
        db.query(Publication).delete()
        db.commit()
    finally:
        db.close()

    return TestClient(app)


class FakeProwlarr:
    def __init__(self, releases):
        self.releases = releases

    def search(self, query, categories=None, indexer_ids=None):
        return self.releases


def _release(title, guid="g1", seeders=10, indexer_id=1, info_hash=None):
    from kioskarr.prowlarr_client import Release

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


def _make_publication(title="Ouest France", target_dir="/tmp/kioskarr-test-library"):
    from kioskarr.db import SessionLocal
    from kioskarr.models import Publication

    db = SessionLocal()
    try:
        pub = Publication(title=title, target_dir=target_dir)
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


def _make_grab(publication_id, identifier, status):
    from kioskarr.db import SessionLocal
    from kioskarr.models import Grab

    db = SessionLocal()
    try:
        db.add(
            Grab(
                publication_id=publication_id,
                release_title="Some Release",
                release_guid="existing-guid",
                identifier=identifier,
                status=status,
            )
        )
        db.commit()
    finally:
        db.close()


def test_preview_returns_confidence_score(client, monkeypatch):
    monkeypatch.setattr(
        "kioskarr.api.search.ProwlarrClient",
        lambda base_url, api_key: FakeProwlarr([_release("Ouest.France.Du.22.Juin.2026.FR.[PDF]-G11")]),
    )

    response = client.get("/search/preview", params={"title": "Ouest France"})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["match"]["score"] > 80
    assert body[0]["match"]["meets_threshold"] is True
    assert body[0]["match"]["already_owned"] is False
    assert body[0]["match"]["already_in_flight"] is False
    assert body[0]["parsed"]["identifier"] == "2026-06-22"


def test_preview_uses_aliases_to_boost_score(client, monkeypatch):
    monkeypatch.setattr(
        "kioskarr.api.search.ProwlarrClient",
        lambda base_url, api_key: FakeProwlarr([_release("Nat.Geo.Aout.2026.FR.[PDF]-G11")]),
    )

    response = client.get(
        "/search/preview", params={"title": "National Geographic", "aliases": ["Nat Geo"]}
    )

    body = response.json()
    assert body[0]["match"]["score"] > 90


def test_preview_flags_already_owned_issue(client, monkeypatch):
    pub_id = _make_publication()
    _make_issue(pub_id, "2026-06-22")
    monkeypatch.setattr(
        "kioskarr.api.search.ProwlarrClient",
        lambda base_url, api_key: FakeProwlarr([_release("Ouest.France.Du.22.Juin.2026.FR.[PDF]-G11")]),
    )

    response = client.get(
        "/search/preview", params={"title": "Ouest France", "publication_id": pub_id}
    )

    assert response.json()[0]["match"]["already_owned"] is True


def test_preview_flags_already_in_flight_grab(client, monkeypatch):
    from kioskarr.models import GrabStatus

    pub_id = _make_publication()
    _make_grab(pub_id, "2026-06-22", GrabStatus.downloading)
    monkeypatch.setattr(
        "kioskarr.api.search.ProwlarrClient",
        lambda base_url, api_key: FakeProwlarr([_release("Ouest.France.Du.22.Juin.2026.FR.[PDF]-G11")]),
    )

    response = client.get(
        "/search/preview", params={"title": "Ouest France", "publication_id": pub_id}
    )

    assert response.json()[0]["match"]["already_in_flight"] is True


def test_preview_without_publication_id_never_flags_owned_or_in_flight(client, monkeypatch):
    pub_id = _make_publication()
    _make_issue(pub_id, "2026-06-22")
    monkeypatch.setattr(
        "kioskarr.api.search.ProwlarrClient",
        lambda base_url, api_key: FakeProwlarr([_release("Ouest.France.Du.22.Juin.2026.FR.[PDF]-G11")]),
    )

    response = client.get("/search/preview", params={"title": "Ouest France"})

    match = response.json()[0]["match"]
    assert match["already_owned"] is False
    assert match["already_in_flight"] is False


def test_preview_sorts_by_date_descending(client, monkeypatch):
    releases = [
        _release("Ouest.France.Du.20.Juin.2026.FR.[PDF]-G11", guid="g20"),
        _release("Ouest.France.Du.22.Juin.2026.FR.[PDF]-G11", guid="g22"),
        _release("Ouest.France.Du.21.Juin.2026.FR.[PDF]-G11", guid="g21"),
        _release("Undateable Release With No Issue Number", guid="g-none"),
    ]
    monkeypatch.setattr(
        "kioskarr.api.search.ProwlarrClient", lambda base_url, api_key: FakeProwlarr(releases)
    )

    response = client.get("/search/preview", params={"title": "Ouest France"})

    identifiers = [r["parsed"]["identifier"] for r in response.json()]
    assert identifiers == ["2026-06-22", "2026-06-21", "2026-06-20", None]


def test_preview_requires_prowlarr_api_key(client):
    from kioskarr.app_settings import get_app_settings
    from kioskarr.db import SessionLocal

    db = SessionLocal()
    try:
        get_app_settings(db).prowlarr_api_key = ""
        db.commit()
    finally:
        db.close()

    response = client.get("/search/preview", params={"title": "Ouest France"})

    assert response.status_code == 400

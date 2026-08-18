import io
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

ATOM = "{http://www.w3.org/2005/Atom}"
PLACEHOLDER_COVER_FILE = Path(__file__).resolve().parent.parent / "kioskarr" / "static" / "opds-placeholder-cover.jpg"
_CBR_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "issue.cbr"


@pytest.fixture
def client():
    # Same pattern as tests/test_auth.py: main.app (and its DB) is a process-wide
    # singleton shared across every test in this file — reset everything these
    # tests touch before each one so they're order-independent.
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


def _make_issue(
    publication_id, identifier, file_path, source_release_title="Some.Release.Title", imported_at=None
):
    from kioskarr.db import SessionLocal
    from kioskarr.models import Issue

    db = SessionLocal()
    try:
        issue = Issue(
            publication_id=publication_id,
            identifier=identifier,
            file_path=str(file_path),
            source_release_title=source_release_title,
        )
        if imported_at is not None:
            issue.imported_at = imported_at
        db.add(issue)
        db.commit()
        db.refresh(issue)
        return issue.id
    finally:
        db.close()


def test_root_feed_lists_one_entry_per_publication(client):
    pub1 = _make_publication(title="Ouest France")
    pub2 = _make_publication(title="Science et Vie")

    response = client.get("/opds")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/atom+xml;profile=opds-catalog;kind=navigation"
    feed = ET.fromstring(response.content)
    entries = feed.findall(f"{ATOM}entry")
    assert len(entries) == 2
    titles = {entry.find(f"{ATOM}title").text for entry in entries}
    assert titles == {"Ouest France", "Science et Vie"}
    for entry in entries:
        link = entry.find(f"{ATOM}link")
        assert link.get("rel") == "subsection"
        assert link.get("type") == "application/atom+xml;profile=opds-catalog;kind=acquisition"
    assert any(f"/opds/publications/{pub1}" in e.find(f"{ATOM}link").get("href") for e in entries)
    assert any(f"/opds/publications/{pub2}" in e.find(f"{ATOM}link").get("href") for e in entries)


def test_root_feed_entry_with_no_issues_links_placeholder_cover(client):
    _make_publication()  # no issues — nothing to borrow a cover from

    response = client.get("/opds")

    entry = ET.fromstring(response.content).find(f"{ATOM}entry")
    links = entry.findall(f"{ATOM}link")
    rels = {link.get("rel") for link in links}
    assert "http://opds-spec.org/image" in rels
    assert "http://opds-spec.org/image/thumbnail" in rels
    for link in links:
        if link.get("rel", "").startswith("http://opds-spec.org/image"):
            assert link.get("href") == "/static/opds-placeholder-cover.jpg"
            assert link.get("type") == "image/jpeg"


def test_root_feed_entry_with_issues_links_latest_issues_cover(client, tmp_path):
    from datetime import datetime, timezone

    pub = _make_publication()
    file_path = tmp_path / "issue.pdf"
    file_path.write_bytes(b"content")
    older_id = _make_issue(
        pub, "2026-08-01", file_path, imported_at=datetime(2026, 8, 1, tzinfo=timezone.utc)
    )
    file_path2 = tmp_path / "issue2.pdf"
    file_path2.write_bytes(b"content2")
    newer_id = _make_issue(
        pub, "2026-08-13", file_path2, imported_at=datetime(2026, 8, 13, tzinfo=timezone.utc)
    )

    response = client.get("/opds")

    entry = ET.fromstring(response.content).find(f"{ATOM}entry")
    link = entry.find(f"{ATOM}link[@rel='http://opds-spec.org/image']")
    assert link.get("href") == f"/opds/issues/{newer_id}/cover"
    assert older_id != newer_id  # sanity check the two issues are actually distinct


def test_publication_feed_lists_issues_with_correct_mime_types(client, tmp_path):
    pub = _make_publication()
    pdf_path = tmp_path / "issue.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake pdf content")
    cbz_path = tmp_path / "issue.cbz"
    cbz_path.write_bytes(b"fake cbz content")
    _make_issue(pub, "2026-08-13", pdf_path)
    _make_issue(pub, "2026-08-14", cbz_path)

    response = client.get(f"/opds/publications/{pub}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/atom+xml;profile=opds-catalog;kind=acquisition"
    feed = ET.fromstring(response.content)
    entries = feed.findall(f"{ATOM}entry")
    assert len(entries) == 2
    links_by_type = {
        entry.find(f"{ATOM}link").get("type"): entry.find(f"{ATOM}link")
        for entry in entries
    }
    assert links_by_type["application/pdf"].get("rel") == "http://opds-spec.org/acquisition"
    assert "vnd.comicbook+zip" in links_by_type["application/vnd.comicbook+zip"].get("type")


def test_publication_feed_issue_entries_link_to_cover_endpoint(client, tmp_path):
    pub = _make_publication()
    file_path = tmp_path / "issue.pdf"
    file_path.write_bytes(b"content")
    issue_id = _make_issue(pub, "2026-08-13", file_path)

    response = client.get(f"/opds/publications/{pub}")

    entry = ET.fromstring(response.content).find(f"{ATOM}entry")
    links = entry.findall(f"{ATOM}link")
    rels = {link.get("rel") for link in links}
    assert "http://opds-spec.org/image" in rels
    assert "http://opds-spec.org/image/thumbnail" in rels
    for link in links:
        if link.get("rel", "").startswith("http://opds-spec.org/image"):
            assert link.get("href") == f"/opds/issues/{issue_id}/cover"
            assert link.get("type") == "image/jpeg"


def test_publication_feed_unknown_mime_falls_back_to_octet_stream(client, tmp_path):
    pub = _make_publication()
    weird_path = tmp_path / "issue.xyz"
    weird_path.write_bytes(b"???")
    _make_issue(pub, "2026-08-13", weird_path)

    response = client.get(f"/opds/publications/{pub}")

    feed = ET.fromstring(response.content)
    link = feed.find(f"{ATOM}entry").find(f"{ATOM}link")
    assert link.get("type") == "application/octet-stream"


def test_publication_feed_404_for_unknown_publication(client):
    response = client.get("/opds/publications/999999")
    assert response.status_code == 404


def test_download_issue_returns_file_bytes(client, tmp_path):
    pub = _make_publication()
    file_path = tmp_path / "issue.pdf"
    file_path.write_bytes(b"real pdf bytes here")
    issue_id = _make_issue(pub, "2026-08-13", file_path)

    response = client.get(f"/opds/issues/{issue_id}/download")

    assert response.status_code == 200
    assert response.content == b"real pdf bytes here"
    assert response.headers["content-type"] == "application/pdf"


def test_download_issue_404_when_row_missing(client):
    response = client.get("/opds/issues/999999/download")
    assert response.status_code == 404


def test_download_issue_404_when_file_deleted_from_disk(client, tmp_path):
    pub = _make_publication()
    file_path = tmp_path / "gone.pdf"
    file_path.write_bytes(b"will be deleted")
    issue_id = _make_issue(pub, "2026-08-13", file_path)
    file_path.unlink()

    response = client.get(f"/opds/issues/{issue_id}/download")

    assert response.status_code == 404


def _write_real_pdf(path):
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument.new()
    pdf.new_page(200, 280)
    pdf.save(str(path))


def _write_real_cbz(path, image_names=("001.jpg", "002.jpg")):
    import io
    import zipfile

    from PIL import Image

    with zipfile.ZipFile(path, "w") as archive:
        for name in image_names:
            buf = io.BytesIO()
            Image.new("RGB", (50, 70), color=(200, 100, 50)).save(buf, "JPEG")
            archive.writestr(name, buf.getvalue())


def _write_real_epub(path):
    container_xml = (
        '<?xml version="1.0"?>'
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    opf = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Test</dc:title></metadata>'
        '<manifest><item id="cover-img" href="cover.jpg" media-type="image/jpeg" '
        'properties="cover-image"/>'
        '<item id="page1" href="page1.xhtml" media-type="application/xhtml+xml"/></manifest>'
        '<spine><itemref idref="page1"/></spine></package>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", container_xml)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/page1.xhtml", "<html><body>hi</body></html>")
        buf = io.BytesIO()
        Image.new("RGB", (60, 90), color=(50, 60, 70)).save(buf, "JPEG")
        archive.writestr("OEBPS/cover.jpg", buf.getvalue())


def test_issue_cover_generates_real_jpeg_from_pdf(client, tmp_path):
    from PIL import Image

    pub = _make_publication()
    pdf_path = tmp_path / "issue.pdf"
    _write_real_pdf(pdf_path)
    issue_id = _make_issue(pub, "2026-08-13", pdf_path)

    response = client.get(f"/opds/issues/{issue_id}/cover")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    image = Image.open(io.BytesIO(response.content))
    assert image.format == "JPEG"
    assert image.width > 0 and image.height > 0


def test_issue_cover_generates_real_jpeg_from_cbz(client, tmp_path):
    from PIL import Image

    pub = _make_publication()
    cbz_path = tmp_path / "issue.cbz"
    _write_real_cbz(cbz_path)
    issue_id = _make_issue(pub, "2026-08-13", cbz_path)

    response = client.get(f"/opds/issues/{issue_id}/cover")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    image = Image.open(io.BytesIO(response.content))
    assert image.format == "JPEG"


def test_issue_cover_falls_back_to_placeholder_for_unsupported_format(client, tmp_path):
    pub = _make_publication()
    mobi_path = tmp_path / "issue.mobi"
    mobi_path.write_bytes(b"not really a mobi")
    issue_id = _make_issue(pub, "2026-08-13", mobi_path)

    response = client.get(f"/opds/issues/{issue_id}/cover")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == PLACEHOLDER_COVER_FILE.read_bytes()


def test_issue_cover_generates_real_jpeg_from_epub(client, tmp_path):
    from PIL import Image

    pub = _make_publication()
    epub_path = tmp_path / "issue.epub"
    _write_real_epub(epub_path)
    issue_id = _make_issue(pub, "2026-08-13", epub_path)

    response = client.get(f"/opds/issues/{issue_id}/cover")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    image = Image.open(io.BytesIO(response.content))
    assert image.format == "JPEG"


@pytest.mark.skipif(not _CBR_FIXTURE.is_file(), reason="CBR fixture not present")
def test_issue_cover_generates_real_jpeg_from_cbr(client, tmp_path):
    from PIL import Image

    pub = _make_publication()
    cbr_path = tmp_path / "issue.cbr"
    cbr_path.write_bytes(_CBR_FIXTURE.read_bytes())
    issue_id = _make_issue(pub, "2026-08-13", cbr_path)

    response = client.get(f"/opds/issues/{issue_id}/cover")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    image = Image.open(io.BytesIO(response.content))
    assert image.format == "JPEG"


def test_issue_cover_falls_back_to_placeholder_for_corrupt_pdf(client, tmp_path):
    pub = _make_publication()
    bad_pdf = tmp_path / "issue.pdf"
    bad_pdf.write_bytes(b"not a real pdf")
    issue_id = _make_issue(pub, "2026-08-13", bad_pdf)

    response = client.get(f"/opds/issues/{issue_id}/cover")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"


def test_issue_cover_404_for_unknown_issue(client):
    response = client.get("/opds/issues/999999/cover")
    assert response.status_code == 404


def test_issue_cover_is_cached_not_regenerated(client, tmp_path):
    pub = _make_publication()
    pdf_path = tmp_path / "issue.pdf"
    _write_real_pdf(pdf_path)
    issue_id = _make_issue(pub, "2026-08-13", pdf_path)

    first = client.get(f"/opds/issues/{issue_id}/cover")
    cover_path = pdf_path.with_suffix(".jpg")
    assert cover_path.is_file()
    first_mtime = cover_path.stat().st_mtime_ns

    second = client.get(f"/opds/issues/{issue_id}/cover")

    assert first.content == second.content
    assert cover_path.stat().st_mtime_ns == first_mtime  # not regenerated


def test_issue_cover_by_token_works_with_no_auth(client, tmp_path):
    pub = _make_publication()
    pdf_path = tmp_path / "issue.pdf"
    _write_real_pdf(pdf_path)
    issue_id = _make_issue(pub, "2026-08-13", pdf_path)
    token = _opds_token(client)
    client.patch("/settings", json={"admin_password": "hunter2"})

    response = client.get(f"/opds/token/{token}/issues/{issue_id}/cover")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"


@pytest.mark.parametrize("format_preference", ["pdf", "cbr", "cbz", "epub", "mobi", "any"])
def test_create_publication_accepts_every_format_preference(client, tmp_path, format_preference):
    response = client.post(
        "/publications",
        json={
            "title": "Ouest France",
            "target_dir": str(tmp_path),
            "format_preference": format_preference,
        },
    )

    assert response.status_code == 201
    assert response.json()["format_preference"] == format_preference


def test_opds_open_when_no_password_set(client):
    _make_publication()
    response = client.get("/opds")
    assert response.status_code == 200


def test_opds_requires_credentials_once_password_set(client):
    client.patch("/settings", json={"admin_password": "hunter2"})

    response = client.get("/opds")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Basic realm="Kioskarr"'


def test_opds_basic_auth_succeeds(client):
    client.patch("/settings", json={"admin_username": "admin", "admin_password": "hunter2"})

    response = client.get("/opds", auth=("admin", "hunter2"))

    assert response.status_code == 200


def test_opds_basic_auth_wrong_password_fails(client):
    client.patch("/settings", json={"admin_password": "hunter2"})

    response = client.get("/opds", auth=("admin", "wrong"))

    assert response.status_code == 401


def test_opds_session_cookie_also_works(client):
    client.patch("/settings", json={"admin_username": "admin", "admin_password": "hunter2"})
    client.post("/auth/login", json={"username": "admin", "password": "hunter2"})

    response = client.get("/opds")

    assert response.status_code == 200


def _opds_token(client):
    return client.get("/settings").json()["opds_token"]


def test_settings_exposes_a_real_opds_token(client):
    body = client.get("/settings").json()
    assert len(body["opds_token"]) > 10


def test_regenerate_opds_token_changes_it_and_invalidates_the_old_one(client):
    old_token = _opds_token(client)

    response = client.patch("/settings", json={"regenerate_opds_token": True})

    assert response.status_code == 200
    new_token = response.json()["opds_token"]
    assert new_token != old_token
    assert len(new_token) > 10
    # Old token no longer works...
    assert client.get(f"/opds/token/{old_token}").status_code == 404
    # ...new one does.
    assert client.get(f"/opds/token/{new_token}").status_code == 200


def test_regenerate_opds_token_false_is_a_no_op(client):
    old_token = _opds_token(client)

    response = client.patch("/settings", json={"regenerate_opds_token": False})

    assert response.json()["opds_token"] == old_token


def test_token_root_feed_works_with_no_auth_even_when_password_set(client):
    # Fetch the token while still open — /settings itself requires a session once a
    # password is set (no Basic Auth fallback there), so this has to happen first.
    token = _opds_token(client)
    client.patch("/settings", json={"admin_password": "hunter2"})

    response = client.get(f"/opds/token/{token}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/atom+xml;profile=opds-catalog;kind=navigation"


def test_token_route_404s_for_wrong_token(client):
    client.patch("/settings", json={"admin_password": "hunter2"})

    response = client.get("/opds/token/not-the-real-token")

    assert response.status_code == 404


def test_token_root_feed_links_stay_within_token_scope(client):
    pub = _make_publication(title="Ouest France")
    token = _opds_token(client)

    response = client.get(f"/opds/token/{token}")

    feed = ET.fromstring(response.content)
    entry_link = feed.find(f"{ATOM}entry").find(f"{ATOM}link")
    assert entry_link.get("href") == f"/opds/token/{token}/publications/{pub}"
    start_link = next(link for link in feed.findall(f"{ATOM}link") if link.get("rel") == "start")
    assert start_link.get("href") == f"/opds/token/{token}"


def test_token_publication_feed_download_link_stays_within_token_scope(client, tmp_path):
    pub = _make_publication()
    file_path = tmp_path / "issue.pdf"
    file_path.write_bytes(b"content")
    issue_id = _make_issue(pub, "2026-08-13", file_path)
    token = _opds_token(client)

    response = client.get(f"/opds/token/{token}/publications/{pub}")

    link = ET.fromstring(response.content).find(f"{ATOM}entry").find(f"{ATOM}link")
    assert link.get("href") == f"/opds/token/{token}/issues/{issue_id}/download"


def test_token_download_works_with_no_auth(client, tmp_path):
    pub = _make_publication()
    file_path = tmp_path / "issue.pdf"
    file_path.write_bytes(b"token download bytes")
    issue_id = _make_issue(pub, "2026-08-13", file_path)
    token = _opds_token(client)
    client.patch("/settings", json={"admin_password": "hunter2"})

    response = client.get(f"/opds/token/{token}/issues/{issue_id}/download")

    assert response.status_code == 200
    assert response.content == b"token download bytes"

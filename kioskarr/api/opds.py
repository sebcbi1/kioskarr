"""OPDS 1.2 catalog feed (https://specs.opds.io/opds-1.2) — lets any generic OPDS
client (Komga, Kavita, Calibre-web/COPS, e-reader apps like Panels/Chunky/KOReader)
browse and download already-imported issues over plain HTTP. Two-level hierarchy:
a navigation feed of publications, each linking to an acquisition feed of that
publication's issues, each linking to the actual downloadable file.

Built with stdlib xml.etree.ElementTree rather than string templating — automatic
escaping means a publication title/identifier containing "&"/"<" can't corrupt the
feed, which hand-rolled f-string XML would be prone to.

Two parallel entry points share the same feed-building logic below (parameterized by
`base`, the path prefix used for every href in the generated feed, so links stay
within whichever mode fetched them):
  - `router` (mounted under /opds): session-or-Basic-Auth protected, for browsers and
    OPDS clients that can answer a 401 challenge.
  - `token_router` (mounted under /opds/token/{token}): unauthenticated at the HTTP
    layer, but every route validates `token` against AppSettings.opds_token itself.
    For clients that only ever send a bare URL with no way to answer an auth
    challenge — e.g. Mihon's Kavita extension, repurposed as a generic OPDS client.
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from kioskarr.app_settings import get_app_settings
from kioskarr.db import get_db
from kioskarr.models import Issue, Publication

router = APIRouter(prefix="/opds", tags=["opds"])
token_router = APIRouter(prefix="/opds/token/{token}", tags=["opds"])

ATOM_NS = "http://www.w3.org/2005/Atom"
ET.register_namespace("", ATOM_NS)  # unprefixed Atom elements, matching OPDS convention

NAV_TYPE = "application/atom+xml;profile=opds-catalog;kind=navigation"
ACQ_TYPE = "application/atom+xml;profile=opds-catalog;kind=acquisition"

# Modern, non-deprecated MIME types (application/x-cbz|x-cbr were deprecated by IANA
# in 2017 and some current OPDS readers no longer recognize them).
_EXTENSION_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".epub": "application/epub+zip",
    ".cbz": "application/vnd.comicbook+zip",
    ".cbr": "application/vnd.comicbook-rar",
    ".mobi": "application/x-mobipocket-ebook",
}


def _mime_type_for(file_path: str) -> str:
    return _EXTENSION_MIME_TYPES.get(Path(file_path).suffix.lower(), "application/octet-stream")


def _rfc3339(dt: datetime) -> str:
    # SQLite has no native timezone-aware storage — imported_at comes back naive.
    # It's written via func.now(), which is UTC, so treat a naive value as UTC
    # rather than emitting an RFC3339 <updated> with no timezone designator at all.
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def _tag(name: str) -> str:
    return f"{{{ATOM_NS}}}{name}"


def _feed_root(feed_id: str, title: str, self_href: str, self_type: str, root_href: str) -> ET.Element:
    feed = ET.Element(_tag("feed"))
    ET.SubElement(feed, _tag("id")).text = feed_id
    ET.SubElement(feed, _tag("title")).text = title
    ET.SubElement(feed, _tag("updated")).text = _rfc3339(datetime.now(timezone.utc))
    author = ET.SubElement(feed, _tag("author"))
    ET.SubElement(author, _tag("name")).text = "Kioskarr"
    ET.SubElement(feed, _tag("link"), rel="self", href=self_href, type=self_type)
    ET.SubElement(feed, _tag("link"), rel="start", href=root_href, type=NAV_TYPE)
    return feed


def _serialize(feed: ET.Element) -> bytes:
    return ET.tostring(feed, encoding="utf-8", xml_declaration=True)


def _require_valid_token(token: str, db: Session) -> None:
    app_settings = get_app_settings(db)
    if not app_settings.opds_token or token != app_settings.opds_token:
        raise HTTPException(404, "Not found")


def _build_root_feed(db: Session, base: str) -> Response:
    feed = _feed_root("urn:kioskarr:root", "Kioskarr", self_href=base, self_type=NAV_TYPE, root_href=base)
    publications = db.query(Publication).order_by(Publication.title).all()
    for pub in publications:
        entry = ET.SubElement(feed, _tag("entry"))
        ET.SubElement(entry, _tag("id")).text = f"urn:kioskarr:publication:{pub.id}"
        ET.SubElement(entry, _tag("title")).text = pub.title
        ET.SubElement(entry, _tag("updated")).text = _rfc3339(datetime.now(timezone.utc))
        ET.SubElement(
            entry, _tag("link"), rel="subsection", href=f"{base}/publications/{pub.id}", type=ACQ_TYPE
        )
    return Response(content=_serialize(feed), media_type=NAV_TYPE)


def _build_publication_feed(db: Session, base: str, publication_id: int) -> Response:
    publication = db.get(Publication, publication_id)
    if publication is None:
        raise HTTPException(404, "Publication not found")

    feed = _feed_root(
        f"urn:kioskarr:publication:{publication.id}",
        publication.title,
        self_href=f"{base}/publications/{publication.id}",
        self_type=ACQ_TYPE,
        root_href=base,
    )
    issues = (
        db.query(Issue)
        .filter(Issue.publication_id == publication.id)
        .order_by(Issue.imported_at.desc())
        .all()
    )
    for issue in issues:
        entry = ET.SubElement(feed, _tag("entry"))
        ET.SubElement(entry, _tag("id")).text = f"urn:kioskarr:issue:{issue.id}"
        ET.SubElement(entry, _tag("title")).text = f"{publication.title} — {issue.identifier}"
        ET.SubElement(entry, _tag("updated")).text = _rfc3339(issue.imported_at)
        ET.SubElement(entry, _tag("content"), type="text").text = issue.source_release_title
        ET.SubElement(
            entry,
            _tag("link"),
            rel="http://opds-spec.org/acquisition",
            href=f"{base}/issues/{issue.id}/download",
            type=_mime_type_for(issue.file_path),
        )
    return Response(content=_serialize(feed), media_type=ACQ_TYPE)


def _issue_file_response(db: Session, issue_id: int) -> FileResponse:
    issue = db.get(Issue, issue_id)
    if issue is None:
        raise HTTPException(404, "Issue not found")
    path = Path(issue.file_path)
    if not path.is_file():
        raise HTTPException(404, "Issue file is no longer on disk")
    return FileResponse(path, media_type=_mime_type_for(issue.file_path), filename=path.name)


# --- Session-or-Basic-Auth routes (protected by main.py's require_auth_or_basic) ---


@router.get("")
def root_feed(db: Session = Depends(get_db)) -> Response:
    return _build_root_feed(db, "/opds")


@router.get("/publications/{publication_id}")
def publication_feed(publication_id: int, db: Session = Depends(get_db)) -> Response:
    return _build_publication_feed(db, "/opds", publication_id)


@router.get("/issues/{issue_id}/download")
def download_issue(issue_id: int, db: Session = Depends(get_db)) -> FileResponse:
    return _issue_file_response(db, issue_id)


# --- Token-in-URL routes — unprotected at the HTTP layer, self-authenticating ---


@token_router.get("")
def root_feed_by_token(token: str, db: Session = Depends(get_db)) -> Response:
    _require_valid_token(token, db)
    return _build_root_feed(db, f"/opds/token/{token}")


@token_router.get("/publications/{publication_id}")
def publication_feed_by_token(token: str, publication_id: int, db: Session = Depends(get_db)) -> Response:
    _require_valid_token(token, db)
    return _build_publication_feed(db, f"/opds/token/{token}", publication_id)


@token_router.get("/issues/{issue_id}/download")
def download_issue_by_token(token: str, issue_id: int, db: Session = Depends(get_db)) -> FileResponse:
    _require_valid_token(token, db)
    return _issue_file_response(db, issue_id)

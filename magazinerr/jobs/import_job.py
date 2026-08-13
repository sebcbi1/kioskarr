"""Poll qBittorrent for completed grabs, re-parse the actual file name, and either
import it into the library or flag it for manual review (never guess silently).
"""

import logging
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from magazinerr.config import settings
from magazinerr.matcher import is_confident_match
from magazinerr.models import Grab, GrabStatus, Issue, Publication, ReviewItem
from magazinerr.parser import parse
from magazinerr.qbittorrent_client import QBittorrentClient

logger = logging.getLogger(__name__)


def _pick_main_file(files: list[dict], format_preference: str) -> dict | None:
    if not files:
        return None
    candidates = files
    if format_preference != "any":
        preferred = [f for f in files if f.get("name", "").lower().endswith(f".{format_preference}")]
        if preferred:
            candidates = preferred
    return max(candidates, key=lambda f: f.get("size", 0))


def _import_file(source_path: Path, target_dir: Path, identifier: str, title: str) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{identifier} - {title}{source_path.suffix}"
    try:
        target_path.hardlink_to(source_path)
    except OSError:
        shutil.copy2(source_path, target_path)
    return target_path


def _downloads_root(torrent: dict) -> Path:
    """The base directory to join a torrent's file names against. qBittorrent
    reports its own save_path, which is only directly usable if this process
    runs on the same host/filesystem; qbittorrent_downloads_local_path overrides
    it for a mounted/synced copy of that directory reachable at a different path.
    """
    if settings.qbittorrent_downloads_local_path:
        return Path(settings.qbittorrent_downloads_local_path)
    return Path(torrent["save_path"])


def _match_torrent(grab: Grab, torrents: list[dict]) -> dict | None:
    if grab.torrent_hash:
        for torrent in torrents:
            if torrent["hash"] == grab.torrent_hash:
                return torrent
    for torrent in torrents:
        if torrent.get("name") == grab.release_title:
            grab.torrent_hash = torrent["hash"]
            return torrent
    return None


def _flag_for_review(db: Session, grab: Grab, file_path: str, reason: str) -> None:
    grab.status = GrabStatus.needs_review
    db.add(
        ReviewItem(
            grab_id=grab.id,
            file_path=file_path,
            reason=reason,
            candidate_publication_id=grab.publication_id,
        )
    )
    db.commit()


def import_issue(
    db: Session, grab: Grab, source_path: Path, identifier: str, publication: Publication
) -> Issue:
    """Shared by the automatic import path and the manual-match review resolution."""
    target_path = _import_file(source_path, Path(publication.target_dir), identifier, publication.title)
    issue = Issue(
        publication_id=publication.id,
        identifier=identifier,
        file_path=str(target_path),
        source_release_title=grab.release_title,
    )
    db.add(issue)
    grab.status = GrabStatus.imported
    db.commit()
    db.refresh(issue)
    return issue


def run_import_job(db: Session, qbt: QBittorrentClient) -> dict:
    torrents = qbt.list_torrents(category=settings.qbittorrent_category)

    pending_grabs = (
        db.query(Grab)
        .filter(Grab.status.in_([GrabStatus.downloading, GrabStatus.completed]))
        .all()
    )

    imported: list[str] = []
    flagged_for_review: list[int] = []

    for grab in pending_grabs:
        torrent = _match_torrent(grab, torrents)
        if torrent is None:
            continue
        if torrent.get("progress", 0) < 1:
            continue  # still downloading

        publication = grab.publication
        try:
            files = qbt.get_files(torrent["hash"])
        except Exception:
            logger.exception("Failed to list files for grab %s", grab.id)
            continue

        main_file = _pick_main_file(files, publication.format_preference.value)
        if main_file is None:
            _flag_for_review(db, grab, torrent.get("content_path", ""), "no files found in torrent")
            flagged_for_review.append(grab.id)
            continue

        source_path = _downloads_root(torrent) / main_file["name"]
        parsed = parse(main_file["name"])

        confident = parsed.identifier is not None and is_confident_match(
            parsed, publication.title, publication.aliases
        )
        if not confident:
            _flag_for_review(db, grab, str(source_path), "low-confidence match on completed file")
            flagged_for_review.append(grab.id)
            continue

        issue = import_issue(db, grab, source_path, parsed.identifier, publication)
        imported.append(issue.file_path)

    return {"imported": imported, "flagged_for_review": flagged_for_review}

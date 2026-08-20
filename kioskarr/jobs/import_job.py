"""Poll qBittorrent for completed grabs, re-parse the actual file name, and either
import it into the library or flag it for manual review (never guess silently).
"""

import logging
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from kioskarr.app_settings import get_app_settings
from kioskarr.matcher import DEFAULT_MATCH_CONFIDENCE_THRESHOLD, is_confident_match
from kioskarr.models import AppSettings, Grab, GrabStatus, Issue, Publication, ReviewItem
from kioskarr.notifications import notify_issue_available
from kioskarr.parser import FORMAT_EXTENSIONS, parse
from kioskarr.qbittorrent_client import QBittorrentClient

logger = logging.getLogger(__name__)


def _file_extension(name: str) -> str:
    return Path(name).suffix.lstrip(".").lower()


def _parse_file_entry(name: str):
    """Parse a torrent file entry for matching purposes. qBittorrent's file
    listing includes the relative path within the torrent — for a folder-based
    multi-file torrent that's "Some Release Folder/actual-file.pdf", and the
    folder name itself often carries its own date (confirmed live: a
    "Journaux Nationaux du Mardi 12 Août 2025" folder). parse() has no concept
    of path boundaries, so it would pick up the *folder's* date/title before
    ever reaching the real file name. Only the basename should ever be parsed
    for identifier/title-matching purposes.
    """
    return parse(Path(name).name)


def _select_issue_file(
    files: list[dict],
    format_preference: str,
    publication_title: str,
    aliases: list[str],
    threshold: float = DEFAULT_MATCH_CONFIDENCE_THRESHOLD,
) -> tuple[dict | None, list[dict]]:
    """Pick the file that represents the issue for this publication.

    Matches by content, the same way search does: recognized magazine/book
    file types only (covers, NFOs, samples never even considered, regardless
    of size), then parsed and confidence-checked by name against the
    publication. This is more precise than a size guess — the real issue
    file isn't always the largest, and it's the only way to handle two real
    release shapes seen live: an "annual archive" bundling one file per month
    of the *same* publication (distinct identifiers, same title), and a
    "national newspapers" bundle for one date containing many *different*
    newspapers (same identifier, only one of which is ours by title).

    Returns (chosen, others):
      - (file, [])              — a clear single answer, possibly the only
                                    typed file in the torrent at all
      - (file, [other files])   — multiple confident matches with different
                                    identifiers: a genuine multi-issue bundle
      - (None, [])              — no recognized file types in the torrent
      - (None, [candidates])    — more than one typed file, but none
                                    confidently matches by name

    Deliberately does NOT fall back to "the largest file" when nothing
    confidently matches and there's more than one candidate: in a bundle of
    several *different* publications for the same date (confirmed live — a
    "Journaux Nationaux" torrent contains a dozen different French dailies for
    one date), the largest file has no relationship to which one is ours —
    guessing risks importing a wholly different newspaper mislabeled as this
    one. Only safe to just use the file outright when it's the sole candidate.
    """
    if not files:
        return None, []

    typed = [f for f in files if _file_extension(f.get("name", "")) in FORMAT_EXTENSIONS]
    if format_preference != "any":
        preferred = [f for f in typed if _file_extension(f.get("name", "")) == format_preference]
        if preferred:
            typed = preferred
    if not typed:
        return None, []

    if len(typed) == 1:
        return typed[0], []

    matches = []
    for f in typed:
        parsed = _parse_file_entry(f["name"])
        if parsed.identifier is not None and is_confident_match(
            parsed, publication_title, aliases, threshold
        ):
            matches.append((f, parsed.identifier))

    if not matches:
        return None, typed  # can't tell which of several candidates is ours — don't guess

    distinct_identifiers = {identifier for _, identifier in matches}
    chosen = max((f for f, _ in matches), key=lambda f: f.get("size", 0))
    if len(distinct_identifiers) <= 1:
        return chosen, []  # one issue, possibly duplicated across formats — no ambiguity
    return chosen, [f for f, _ in matches if f is not chosen]  # distinct issues bundled together


def _import_file(source_path: Path, target_dir: Path, identifier: str, title: str) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{identifier} - {title}{source_path.suffix}"
    try:
        target_path.hardlink_to(source_path)
    except OSError:
        shutil.copy2(source_path, target_path)
    return target_path


def _downloads_root(torrent: dict, app_settings: AppSettings) -> Path:
    """The base directory to join a torrent's file names against. qBittorrent
    reports its own save_path, which is only directly usable if this process
    runs on the same host/filesystem; qbittorrent_downloads_local_path overrides
    it for a mounted/synced copy of that directory reachable at a different path.
    """
    if app_settings.qbittorrent_downloads_local_path:
        return Path(app_settings.qbittorrent_downloads_local_path)
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
    notify_issue_available(get_app_settings(db), issue, publication)
    return issue


def run_import_job(db: Session, qbt: QBittorrentClient) -> dict:
    app_settings = get_app_settings(db)
    torrents = qbt.list_torrents(category=app_settings.qbittorrent_category)

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

        main_file, other_matches = _select_issue_file(
            files,
            publication.format_preference.value,
            publication.title,
            publication.aliases,
            app_settings.match_confidence_threshold,
        )
        if main_file is None:
            if other_matches:
                names = ", ".join(f["name"] for f in other_matches)
                reason = (
                    f"torrent has {len(other_matches)} candidate files but none confidently "
                    f"matches this publication by name — won't guess which is ours: {names}"
                )
            else:
                reason = "no recognized magazine/book file types found in torrent"
            _flag_for_review(db, grab, torrent.get("content_path", ""), reason)
            flagged_for_review.append(grab.id)
            continue

        if other_matches:
            names = ", ".join(f["name"] for f in [main_file, *other_matches])
            _flag_for_review(
                db,
                grab,
                torrent.get("content_path", ""),
                f"torrent bundles multiple distinct issues, can't import just one: {names}",
            )
            flagged_for_review.append(grab.id)
            continue

        source_path = _downloads_root(torrent, app_settings) / main_file["name"]
        parsed = _parse_file_entry(main_file["name"])

        confident = parsed.identifier is not None and is_confident_match(
            parsed, publication.title, publication.aliases, app_settings.match_confidence_threshold
        )
        if not confident:
            _flag_for_review(db, grab, str(source_path), "low-confidence match on completed file")
            flagged_for_review.append(grab.id)
            continue

        try:
            issue = import_issue(db, grab, source_path, parsed.identifier, publication)
        except OSError as exc:
            # A single missing/unreachable file (moved, wrong mount, permissions) shouldn't
            # crash the whole tick and leave every other publication's pending grabs stuck.
            logger.exception("Failed to import grab %s from %s", grab.id, source_path)
            _flag_for_review(db, grab, str(source_path), f"import failed: {exc}")
            flagged_for_review.append(grab.id)
            continue
        imported.append(issue.file_path)

    return {"imported": imported, "flagged_for_review": flagged_for_review}

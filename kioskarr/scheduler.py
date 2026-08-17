"""APScheduler wiring for the two background jobs. Kept as module-level singleton
state started/stopped from the FastAPI app lifespan.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from kioskarr.app_settings import get_app_settings
from kioskarr.db import SessionLocal
from kioskarr.jobs.import_job import run_import_job
from kioskarr.jobs.search_job import run_search_job
from kioskarr.models import AppSettings
from kioskarr.prowlarr_client import ProwlarrClient
from kioskarr.qbittorrent_client import QBittorrentClient

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _make_prowlarr_client(app_settings: AppSettings) -> ProwlarrClient:
    return ProwlarrClient(app_settings.prowlarr_url, app_settings.prowlarr_api_key)


def _make_qbt_client(app_settings: AppSettings) -> QBittorrentClient:
    client = QBittorrentClient(
        app_settings.qbittorrent_url, app_settings.qbittorrent_username, app_settings.qbittorrent_password
    )
    client.login()
    client.ensure_category(app_settings.qbittorrent_category)
    return client


def _search_tick() -> None:
    logger.info("search tick starting")
    db = SessionLocal()
    try:
        app_settings = get_app_settings(db)
        try:
            app_settings.require_download_client()
        except RuntimeError as exc:
            logger.info("Skipping search tick — %s", exc)
            return
        grabs = run_search_job(db, _make_prowlarr_client(app_settings), _make_qbt_client(app_settings))
        logger.info("search tick finished — %d new grab(s)", len(grabs))
    finally:
        db.close()


def _import_tick() -> None:
    logger.info("import tick starting")
    db = SessionLocal()
    try:
        app_settings = get_app_settings(db)
        try:
            app_settings.require_download_client()
        except RuntimeError as exc:
            logger.info("Skipping import tick — %s", exc)
            return
        result = run_import_job(db, _make_qbt_client(app_settings))
        logger.info(
            "import tick finished — %d imported, %d flagged for review",
            len(result["imported"]),
            len(result["flagged_for_review"]),
        )
    finally:
        db.close()


def start_scheduler(app_settings: AppSettings) -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _search_tick, "interval", hours=app_settings.search_interval_hours, id="search_job"
    )
    scheduler.add_job(
        _import_tick, "interval", minutes=app_settings.import_interval_minutes, id="import_job"
    )
    scheduler.start()
    _scheduler = scheduler
    return scheduler


def reschedule(app_settings: AppSettings) -> None:
    """Apply new search/import intervals immediately instead of waiting for a
    restart — called from the Settings API when either interval changes."""
    if _scheduler is None:
        return
    _scheduler.reschedule_job(
        "search_job", trigger=IntervalTrigger(hours=app_settings.search_interval_hours)
    )
    _scheduler.reschedule_job(
        "import_job", trigger=IntervalTrigger(minutes=app_settings.import_interval_minutes)
    )


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None

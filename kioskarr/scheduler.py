"""APScheduler wiring for the two background jobs. Kept as module-level singleton
state started/stopped from the FastAPI app lifespan.
"""

from apscheduler.schedulers.background import BackgroundScheduler

from kioskarr.config import settings
from kioskarr.db import SessionLocal
from kioskarr.jobs.import_job import run_import_job
from kioskarr.jobs.search_job import run_search_job
from kioskarr.prowlarr_client import ProwlarrClient
from kioskarr.qbittorrent_client import QBittorrentClient

_scheduler: BackgroundScheduler | None = None


def _make_prowlarr_client() -> ProwlarrClient:
    return ProwlarrClient(settings.prowlarr_url, settings.prowlarr_api_key)


def _make_qbt_client() -> QBittorrentClient:
    client = QBittorrentClient(
        settings.qbittorrent_url, settings.qbittorrent_username, settings.qbittorrent_password
    )
    client.login()
    client.ensure_category(settings.qbittorrent_category)
    return client


def _search_tick() -> None:
    db = SessionLocal()
    try:
        run_search_job(db, _make_prowlarr_client(), _make_qbt_client())
    finally:
        db.close()


def _import_tick() -> None:
    db = SessionLocal()
    try:
        run_import_job(db, _make_qbt_client())
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    scheduler = BackgroundScheduler()
    scheduler.add_job(_search_tick, "interval", hours=settings.search_interval_hours, id="search_job")
    scheduler.add_job(
        _import_tick, "interval", minutes=settings.import_interval_minutes, id="import_job"
    )
    scheduler.start()
    _scheduler = scheduler
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None

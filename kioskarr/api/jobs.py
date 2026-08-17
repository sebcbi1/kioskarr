from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from kioskarr.app_settings import get_app_settings
from kioskarr.db import get_db
from kioskarr.jobs.import_job import run_import_job
from kioskarr.qbittorrent_client import QBittorrentClient

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/import-now")
def import_now(db: Session = Depends(get_db)) -> dict:
    """Trigger the import job immediately instead of waiting for the scheduler
    tick — mirrors POST /publications/{id}/search-now, for testing."""
    app_settings = get_app_settings(db)
    app_settings.require_download_client()
    qbt = QBittorrentClient(
        app_settings.qbittorrent_url, app_settings.qbittorrent_username, app_settings.qbittorrent_password
    )
    qbt.login()
    return run_import_job(db, qbt)

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from magazinerr.config import settings
from magazinerr.db import get_db
from magazinerr.jobs.import_job import run_import_job
from magazinerr.qbittorrent_client import QBittorrentClient

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/import-now")
def import_now(db: Session = Depends(get_db)) -> dict:
    """Trigger the import job immediately instead of waiting for the scheduler
    tick — mirrors POST /publications/{id}/search-now, for testing."""
    settings.require_download_client()
    qbt = QBittorrentClient(
        settings.qbittorrent_url, settings.qbittorrent_username, settings.qbittorrent_password
    )
    qbt.login()
    return run_import_job(db, qbt)

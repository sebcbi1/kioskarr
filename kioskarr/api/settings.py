from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from kioskarr.app_settings import get_app_settings
from kioskarr.db import get_db
from kioskarr.models import AppSettings
from kioskarr.scheduler import reschedule

router = APIRouter(prefix="/settings", tags=["settings"])


class AppSettingsOut(BaseModel):
    prowlarr_url: str
    prowlarr_api_key: str
    qbittorrent_url: str
    qbittorrent_username: str
    qbittorrent_password: str
    qbittorrent_category: str
    qbittorrent_downloads_local_path: str
    library_root: str
    search_interval_hours: float
    import_interval_minutes: float
    default_min_seeders: int
    match_confidence_threshold: float

    model_config = {"from_attributes": True}


class AppSettingsUpdate(BaseModel):
    prowlarr_url: str | None = None
    prowlarr_api_key: str | None = None
    qbittorrent_url: str | None = None
    qbittorrent_username: str | None = None
    qbittorrent_password: str | None = None
    qbittorrent_category: str | None = None
    qbittorrent_downloads_local_path: str | None = None
    library_root: str | None = None
    search_interval_hours: float | None = None
    import_interval_minutes: float | None = None
    default_min_seeders: int | None = None
    match_confidence_threshold: float | None = None


@router.get("", response_model=AppSettingsOut)
def get_settings(db: Session = Depends(get_db)) -> AppSettings:
    return get_app_settings(db)


@router.patch("", response_model=AppSettingsOut)
def update_settings(payload: AppSettingsUpdate, db: Session = Depends(get_db)) -> AppSettings:
    app_settings = get_app_settings(db)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(app_settings, field, value)
    db.commit()
    db.refresh(app_settings)

    # Apply new intervals immediately instead of waiting for a restart.
    if "search_interval_hours" in changes or "import_interval_minutes" in changes:
        reschedule(app_settings)

    return app_settings

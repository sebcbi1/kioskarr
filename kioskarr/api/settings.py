import secrets

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from kioskarr.app_settings import get_app_settings
from kioskarr.auth import hash_password
from kioskarr.db import get_db
from kioskarr.models import AppSettings
from kioskarr.scheduler import reschedule

router = APIRouter(prefix="/settings", tags=["settings"])


class AppSettingsOut(BaseModel):
    prowlarr_url: str
    prowlarr_api_key_set: bool
    qbittorrent_url: str
    qbittorrent_username: str
    qbittorrent_password_set: bool
    qbittorrent_category: str
    qbittorrent_downloads_local_path: str
    library_root: str
    search_interval_hours: float
    import_interval_minutes: float
    default_min_seeders: int
    match_confidence_threshold: float
    admin_username: str
    admin_password_set: bool
    opds_token: str


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
    admin_username: str | None = None
    admin_password: str | None = None
    regenerate_opds_token: bool | None = None


def _to_out(app_settings: AppSettings) -> AppSettingsOut:
    return AppSettingsOut(
        prowlarr_url=app_settings.prowlarr_url,
        prowlarr_api_key_set=bool(app_settings.prowlarr_api_key),
        qbittorrent_url=app_settings.qbittorrent_url,
        qbittorrent_username=app_settings.qbittorrent_username,
        qbittorrent_password_set=bool(app_settings.qbittorrent_password),
        qbittorrent_category=app_settings.qbittorrent_category,
        qbittorrent_downloads_local_path=app_settings.qbittorrent_downloads_local_path,
        library_root=app_settings.library_root,
        search_interval_hours=app_settings.search_interval_hours,
        import_interval_minutes=app_settings.import_interval_minutes,
        default_min_seeders=app_settings.default_min_seeders,
        match_confidence_threshold=app_settings.match_confidence_threshold,
        admin_username=app_settings.admin_username,
        admin_password_set=bool(app_settings.admin_password_hash),
        opds_token=app_settings.opds_token,
    )


@router.get("", response_model=AppSettingsOut)
def get_settings(db: Session = Depends(get_db)) -> AppSettingsOut:
    return _to_out(get_app_settings(db))


@router.patch("", response_model=AppSettingsOut)
def update_settings(payload: AppSettingsUpdate, db: Session = Depends(get_db)) -> AppSettingsOut:
    app_settings = get_app_settings(db)
    changes = payload.model_dump(exclude_unset=True)

    # admin_password isn't a column — it gets hashed into admin_password_hash and
    # the plaintext is never stored or echoed back. Sending "" (or null) explicitly
    # clears it, disabling auth again — same clear-by-empty-string convention as
    # prowlarr_api_key/qbittorrent_password below.
    if "admin_password" in changes:
        new_password = changes.pop("admin_password")
        app_settings.admin_password_hash = hash_password(new_password) if new_password else ""

    # Also not a column — a one-shot action, not a value to store. Any reader app
    # configured with the old token URL stops working until updated with the new one.
    if changes.pop("regenerate_opds_token", None):
        app_settings.opds_token = secrets.token_urlsafe(24)

    for field, value in changes.items():
        setattr(app_settings, field, value)
    db.commit()
    db.refresh(app_settings)

    # Apply new intervals immediately instead of waiting for a restart.
    if "search_interval_hours" in changes or "import_interval_minutes" in changes:
        reschedule(app_settings)

    return _to_out(app_settings)

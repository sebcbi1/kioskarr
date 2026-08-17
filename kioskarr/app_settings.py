"""Accessor for the DB-backed AppSettings singleton row.

Everything except database_url (which has to stay an env var — you need it to
reach the DB before you can query it for anything) lives here instead of
kioskarr.config, so it's editable live via the Settings UI/API without a restart.
"""

from sqlalchemy.orm import Session

from kioskarr.config import settings as env_settings
from kioskarr.models import AppSettings

_SETTINGS_ID = 1


def get_app_settings(db: Session) -> AppSettings:
    app_settings = db.get(AppSettings, _SETTINGS_ID)
    if app_settings is None:
        raise RuntimeError(
            "AppSettings row is missing — ensure_app_settings_seeded() should have "
            "run at startup."
        )
    return app_settings


def ensure_app_settings_seeded(db: Session) -> AppSettings:
    """Create the singleton row on first boot, seeded from whatever's currently in
    .env — so an existing deployment's Prowlarr/qBittorrent credentials carry over
    automatically instead of needing to be retyped into the new Settings page."""
    app_settings = db.get(AppSettings, _SETTINGS_ID)
    if app_settings is not None:
        return app_settings

    app_settings = AppSettings(
        id=_SETTINGS_ID,
        prowlarr_url=env_settings.prowlarr_url,
        prowlarr_api_key=env_settings.prowlarr_api_key,
        qbittorrent_url=env_settings.qbittorrent_url,
        qbittorrent_username=env_settings.qbittorrent_username,
        qbittorrent_password=env_settings.qbittorrent_password,
        qbittorrent_category=env_settings.qbittorrent_category,
        qbittorrent_downloads_local_path=env_settings.qbittorrent_downloads_local_path,
        library_root=env_settings.library_root,
        search_interval_hours=env_settings.search_interval_hours,
        import_interval_minutes=env_settings.import_interval_minutes,
        default_min_seeders=env_settings.default_min_seeders,
        match_confidence_threshold=env_settings.match_confidence_threshold,
    )
    db.add(app_settings)
    db.commit()
    db.refresh(app_settings)
    return app_settings

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Env-var-only bootstrap config. database_url is the sole setting that must
    live here — you need it to reach the DB before you can query it for anything
    else. Every other field below exists only to seed kioskarr.models.AppSettings
    on first boot (see kioskarr.app_settings.ensure_app_settings_seeded); nothing
    outside that seeding path should read them once the app has started.
    """

    model_config = SettingsConfigDict(env_file=".env", env_prefix="KIOSKARR_")

    database_url: str = "sqlite:///./kioskarr.db"

    prowlarr_url: str = "http://localhost:9696"
    prowlarr_api_key: str = ""

    qbittorrent_url: str = "http://localhost:8080"
    qbittorrent_username: str = "admin"
    qbittorrent_password: str = ""
    qbittorrent_category: str = "kioskarr"
    qbittorrent_downloads_local_path: str = ""

    library_root: str = "./library"

    search_interval_hours: float = 4.0
    import_interval_minutes: float = 5.0

    default_min_seeders: int = 1
    match_confidence_threshold: float = 75.0  # rapidfuzz score, 0-100


settings = Settings()

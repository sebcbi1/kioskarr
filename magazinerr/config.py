from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MAGAZINERR_")

    database_url: str = "sqlite:///./magazinerr.db"

    prowlarr_url: str = "http://localhost:9696"
    prowlarr_api_key: str = ""

    qbittorrent_url: str = "http://localhost:8080"
    qbittorrent_username: str = "admin"
    qbittorrent_password: str = ""
    qbittorrent_category: str = "magazinerr"

    library_root: str = "./library"

    search_interval_hours: float = 4.0
    import_interval_minutes: float = 5.0

    default_min_seeders: int = 1
    match_confidence_threshold: float = 75.0  # rapidfuzz score, 0-100

    def require_download_client(self) -> None:
        if not self.prowlarr_api_key:
            raise RuntimeError(
                "MAGAZINERR_PROWLARR_API_KEY is not set — cannot search indexers."
            )
        if not self.qbittorrent_password:
            raise RuntimeError(
                "MAGAZINERR_QBITTORRENT_PASSWORD is not set — cannot control downloads."
            )


settings = Settings()

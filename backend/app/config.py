from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        extra="ignore",
    )

    edinet_api_key: str = ""
    database_url: str = "sqlite:///./data/edinet.db"
    edinet_base_url: str = "https://api.edinet-fsa.go.jp/api/v2"
    site_url: str = ""
    google_site_verification: str = ""
    collection_log_enabled: bool = True
    collection_log_dir: str = "../data/collection-logs"
    external_media_batch_limit: int = 120
    external_media_sleep_news: float = 1.0
    external_media_sleep_trends: float = 3.0


settings = Settings()

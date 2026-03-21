from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки приложения из переменных окружения."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="facebook-ads-stop-bot", alias="APP_NAME")
    app_env: str = Field(default="local", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")

    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="facebook_ads_bot", alias="POSTGRES_DB")
    postgres_user: str = Field(default="facebook_ads_bot", alias="POSTGRES_USER")
    postgres_password: str = Field(default="facebook_ads_bot", alias="POSTGRES_PASSWORD")

    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")
    browser_vendor: str = Field(default="vision", alias="BROWSER_VENDOR")
    vision_api_token: str = Field(default="", alias="VISION_API_TOKEN")
    vision_cloud_api_url: str = Field(
        default="https://v1.empr.cloud/api/v1",
        alias="VISION_CLOUD_API_URL",
    )
    vision_local_api_url: str = Field(
        default="http://127.0.0.1:3030",
        alias="VISION_LOCAL_API_URL",
    )
    vision_timeout_seconds: float = Field(default=10.0, alias="VISION_TIMEOUT_SECONDS")
    worker_scan_interval_seconds: int = Field(default=120, alias="WORKER_SCAN_INTERVAL_SECONDS")
    scanner_stabilize_attempts: int = Field(default=3, alias="SCANNER_STABILIZE_ATTEMPTS")
    scanner_stabilize_delay_ms: int = Field(default=800, alias="SCANNER_STABILIZE_DELAY_MS")
    scanner_scroll_pause_ms: int = Field(default=700, alias="SCANNER_SCROLL_PAUSE_MS")
    scanner_max_no_new_attempts: int = Field(default=3, alias="SCANNER_MAX_NO_NEW_ATTEMPTS")
    scanner_scroll_step_px: int = Field(default=2500, alias="SCANNER_SCROLL_STEP_PX")

    feature_auto_pause: bool = Field(default=False, alias="FEATURE_AUTO_PAUSE")
    feature_auto_resume: bool = Field(default=False, alias="FEATURE_AUTO_RESUME")
    feature_observe_only: bool = Field(default=True, alias="FEATURE_OBSERVE_ONLY")

    @property
    def postgres_dsn(self) -> str:
        return (
            "postgresql+asyncpg://"
            f"{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

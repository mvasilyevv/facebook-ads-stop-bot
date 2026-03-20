from __future__ import annotations

import os
from dataclasses import dataclass


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    return normalized in {"1", "true", "yes", "on", "да"}


def _as_csv_list(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    items = [item.strip() for item in value.split(",")]
    return tuple(item for item in items if item)


@dataclass(slots=True)
class ApiSettings:
    """Настройки API."""

    app_name: str = "Facebook Ads Control API"
    environment: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    debug: bool = True
    docs_enabled: bool = True
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")
    default_browser_host_id: str = "browser-host-local"
    default_profile_id: str = "profile-local"


def load_settings() -> ApiSettings:
    """Загружает настройки из переменных окружения."""

    return ApiSettings(
        app_name=os.getenv("API_APP_NAME", "Facebook Ads Control API"),
        environment=os.getenv("API_ENVIRONMENT", "development"),
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
        log_level=os.getenv("API_LOG_LEVEL", "INFO"),
        debug=_as_bool(os.getenv("API_DEBUG"), default=True),
        docs_enabled=_as_bool(os.getenv("API_DOCS_ENABLED"), default=True),
        cors_origins=_as_csv_list(
            os.getenv("API_CORS_ORIGINS"),
            default=("http://localhost:5173", "http://127.0.0.1:5173"),
        ),
        default_browser_host_id=os.getenv("API_DEFAULT_BROWSER_HOST_ID", "browser-host-local"),
        default_profile_id=os.getenv("API_DEFAULT_PROFILE_ID", "profile-local"),
    )

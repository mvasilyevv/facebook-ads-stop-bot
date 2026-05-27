# -*- coding: utf-8 -*-
"""Конфигурация приложения через Pydantic Settings."""

from __future__ import annotations

import logging
import secrets
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings  # type: ignore[import-not-found]

logger = logging.getLogger(__name__)

# Путь к .env — корень проекта (рядом с run.sh)
_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def _generate_and_persist_api_key() -> str:
    """Генерирует API-ключ и дописывает его в .env."""
    key = secrets.token_urlsafe(32)
    try:
        # Дописываем в конец .env (создаём файл если нет)
        with _ENV_FILE.open("a", encoding="utf-8") as f:
            f.write(f"\nAPI_KEY={key}\n")
        logger.info("API_KEY сгенерирован и сохранён в %s", _ENV_FILE)
    except OSError:
        logger.warning("Не удалось записать API_KEY в .env — ключ действует только до перезапуска")
    return key


class Settings(BaseSettings):
    """Настройки приложения, читаются из env-переменных."""

    # --- Postgres ---
    postgres_host: str = "localhost"
    postgres_port: int = 5433
    postgres_db: str = "fb_stop_bot_v2"
    postgres_user: str = "fb_stop_bot_v2"
    postgres_password: str = "fb_stop_bot_v2"

    # --- Telegram ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- Шифрование (для хранения токенов в БД) ---
    encryption_key: str = ""
    encryption_key_verify: str = ""

    # --- API ---
    api_host: str = "0.0.0.0"
    api_port: int = 8100
    api_key: str = ""
    app_timezone: str = "Europe/Kaliningrad"

    # --- Observer ---
    default_observer_interval_seconds: int = 90

    # --- Vision Anti-detect браузер ---
    vision_x_token: str = ""
    vision_api_url: str = "http://127.0.0.1:3030"
    vision_profile_id: str = ""
    vision_auto_restart_on_missing_cdp: bool = True

    # --- Redis (очередь алёртов) ---
    redis_url: str = "redis://localhost:6380/0"
    alerts_queue_enabled: bool = True

    # --- Sentry (опционально) ---
    sentry_dsn: str = ""
    sentry_environment: str = "production"

    # --- Telegram Mini App ---
    tma_session_ttl_seconds: int = 3600
    web_app_url: str | None = None

    # --- Telegram Daily Digest ---
    digest_enabled: bool = True
    digest_hour: int = 9
    digest_timezone: str = "Europe/Moscow"

    # --- AI Assistant (Claude + OpenAI fallback) ---
    ai_diagnostics_enabled: bool = True
    ai_chat_enabled: bool = True
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.claudehub.fun/v1"
    anthropic_model: str = "claude-sonnet-4.6"
    openai_api_key: str = ""
    openai_base_url: str = "https://gateway.nekocode.app/andromeda/v1"
    openai_model: str = "openai/gpt-5.4-mini"
    ai_diagnostics_cooldown_seconds: int = 1800
    ai_timeout_seconds: int = 20
    ai_max_log_lines: int = 200
    ai_max_tool_iterations: int = 5
    ai_rate_limit_per_hour: int = 30
    # Объяснения причин алертов через AI (приклеиваются к STOP/WARNING в Telegram)
    ai_explain_alerts_enabled: bool = True
    # 8s было мало для сторонних gateway: 💡-объяснение всегда падало по timeout.
    # 20s — компромисс между UX и блокировкой алерта.
    ai_explain_timeout_seconds: float = 20.0

    # --- AdSet.pro (внешний MCP-сервер post-click статистики, см. META_INTEGRATION_PLAN §4.4 / Этап 6) ---
    # Live verify (2026-05-27): AdSet.pro работает как MCP-сервер `platform-stats-mcp`
    # на https://adset.pro/mcp (JSON-RPC 2.0 + Bearer). REST вида /api/stats/query
    # не существует — host api.adset.pro вообще не резолвится.
    adsetpro_mcp_key: str = ""
    adsetpro_base_url: str = "https://adset.pro"
    adsetpro_timeout_seconds: float = 15.0
    # Секрет для аутентификации входящего postback'а от AdSet.pro
    # (header X-Postback-Secret). Пустая строка → endpoint возвращает 503
    # «not configured», чтобы случайно не принимать неавторизованные постбэки.
    adsetpro_postback_secret: str = ""

    # --- Frontend (CORS) ---
    # Origin основного фронта (например, http://localhost:5173). None → CORS не подключаем.
    frontend_origin: str | None = None

    @model_validator(mode="after")
    def _warn_insecure_defaults(self) -> "Settings":
        """Предупреждаем о небезопасных настройках при старте."""
        if self.postgres_password == self.postgres_db:
            logger.warning(
                "Пароль Postgres совпадает с именем БД — "
                "задайте уникальный POSTGRES_PASSWORD для продакшена"
            )
        if not self.encryption_key:
            logger.warning("ENCRYPTION_KEY не задан — шифрование токенов в БД не будет работать")
        if not self.telegram_bot_token:
            logger.warning("TELEGRAM_BOT_TOKEN не задан — Telegram-бот не будет работать")
        if not self.api_key:
            self.api_key = _generate_and_persist_api_key()
        return self

    @property
    def database_url(self) -> str:
        """Строка подключения к Postgres для asyncpg."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{quote_plus(self.postgres_password)}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    model_config = {"env_file": ".env", "extra": "ignore"}


_settings: Settings | None = None


def get_settings() -> Settings:
    """Ленивый синглтон настроек."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings

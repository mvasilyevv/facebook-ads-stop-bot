# -*- coding: utf-8 -*-
"""Конфигурация приложения через Pydantic Settings."""

from __future__ import annotations

import logging
import secrets
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings

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

    # --- Sentry (опционально) ---
    sentry_dsn: str = ""
    sentry_environment: str = "production"

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

# -*- coding: utf-8 -*-
"""DB-authoritative Web App URL для Telegram Mini App.

Окружение используется только одноразовой release/bootstrap-командой. Runtime
читает ``system_config``; строка ``{"url": null}`` является явным tombstone и
не разрешает повторно импортировать значение из окружения.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.config import Settings, get_settings

logger = logging.getLogger(__name__)

_KEY = "web_app_url"


async def load_web_app_url(engine: AsyncEngine) -> str | None:
    """Читает web_app_url из system_config. None — если ключа нет/пусто."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT value FROM system_config WHERE key = :k"),
                {"k": _KEY},
            )
        ).first()
    if not row or not row[0]:
        return None
    value = row[0]
    # asyncpg обычно отдаёт JSONB как dict, но при нестандартном codec — строкой.
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None
    if not isinstance(value, dict):
        return None
    url = value.get("url")
    return url or None


async def bootstrap_web_app_url_from_env(
    engine: AsyncEngine,
    *,
    settings: Settings | None = None,
) -> bool:
    """Однократно импортировать ``WEB_APP_URL`` в отсутствующую DB-строку.

    Существующая строка, включая tombstone с ``null``, всегда выигрывает.
    Повторные и параллельные вызовы безопасны благодаря unique-key + conflict
    handling. Runtime-код эту функцию не вызывает.
    """

    async with engine.connect() as conn:
        exists = await conn.scalar(
            text("SELECT EXISTS (SELECT 1 FROM system_config WHERE key = :key)"),
            {"key": _KEY},
        )
    if exists:
        return False

    resolved_settings = settings or get_settings()
    cleaned = (resolved_settings.web_app_url or "").strip()
    if not cleaned:
        return False

    try:
        parsed = urlsplit(cleaned)
        is_valid = (
            parsed.scheme.lower() == "https"
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        is_valid = False
    if not is_valid:
        # URL deliberately omitted: it can contain query credentials.
        logger.error("WEB_APP_URL bootstrap отклонён: требуется безопасный HTTPS URL")
        raise ValueError("WEB_APP_URL bootstrap requires a valid HTTPS URL")

    payload = json.dumps({"url": cleaned})
    async with engine.begin() as conn:
        inserted = (
            await conn.execute(
                text(
                    """
                    INSERT INTO system_config (key, value, description)
                    VALUES (:key, CAST(:value AS JSONB), :description)
                    ON CONFLICT (key) DO NOTHING
                    RETURNING key
                    """
                ),
                {
                    "key": _KEY,
                    "value": payload,
                    "description": "Web App URL для Telegram Mini App",
                },
            )
        ).first()

    if inserted is not None:
        logger.info("system_config.web_app_url создан из bootstrap environment")
    return inserted is not None


async def save_web_app_url(engine: AsyncEngine, url: str | None) -> None:
    """UPSERT web_app_url в system_config (ON CONFLICT по key). Пусто → null."""
    cleaned = (url or "").strip() or None
    payload = json.dumps({"url": cleaned})
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO system_config (key, value, description)
                VALUES (:k, CAST(:v AS JSONB), :d)
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value, updated_at = NOW()
                """
            ),
            {"k": _KEY, "v": payload, "d": "Web App URL для Telegram Mini App"},
        )


def normalize_web_app_base(raw: str | None) -> str | None:
    """Нормализует web_app base для deep-link кнопок под алертами.

    Возвращает https-base без хвостового слэша, либо None если raw пуст
    или не https (Telegram inline web_app кнопки требуют https-URL).
    """
    if not raw:
        return None
    cleaned = raw.strip().rstrip("/")
    if not cleaned.startswith("https://"):
        return None
    return cleaned


__all__ = [
    "bootstrap_web_app_url_from_env",
    "load_web_app_url",
    "normalize_web_app_base",
    "save_web_app_url",
]

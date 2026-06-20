# -*- coding: utf-8 -*-
"""Хранение Web App URL (Telegram Mini App) в system_config (key='web_app_url').

Меняется без рестарта (как cabinet_autostart). GET-фолбэк: если в БД пусто —
caller подставляет config.web_app_url из .env.

Формат value JSONB: {"url": "https://..."} либо {"url": null} (очистка).
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

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


__all__ = ["load_web_app_url", "save_web_app_url", "normalize_web_app_base"]

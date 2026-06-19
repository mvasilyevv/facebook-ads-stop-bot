# -*- coding: utf-8 -*-
"""Конфиг и pure-хелперы автостарта кабинета по расписанию.

Автостарт — money-критичная фича: в заданное время (UTC, ежедневно) воркер
автоматически включает (enable) объявления ОТСЛЕЖИВАЕМЫХ кампаний (allowlist
из observer_config.campaign_ids) и триггерит observer scan. Без подтверждения.
Список кампаний НЕ дублируется — берётся из «Отслеживаемые кампании».

Хранение конфига — в ``system_config`` (key='cabinet_autostart', value JSONB):
    {
        "enabled": bool,      # фича включена
        "hour_utc": int,      # плановый час (UTC) 0..23
        "minute_utc": int,    # плановая минута 0..59
    }

Pure-хелперы (без I/O):
- is_in_autostart_window — catch-up окно от HH:MM до конца суток UTC (как digest).
- autostart_done_key — Redis-ключ дедупа за день (cabinet:autostart:YYYY-MM-DD).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# Ключ в system_config.
CONFIG_KEY = "cabinet_autostart"

# Дефолт, если строки в system_config ещё нет.
DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "hour_utc": 6,
    "minute_utc": 0,
}

# Redis-ключ дедупа: 26 часов с запасом перекрывают окно следующего дня.
AUTOSTART_DONE_TTL_SECONDS = 26 * 3600
AUTOSTART_DONE_KEY_PREFIX = "cabinet:autostart:"


# ====================== pure helpers ======================


def is_in_autostart_window(now: datetime, hour_utc: int, minute_utc: int) -> bool:
    """True если now попадает в [HH:MM ; конец суток UTC).

    Catch-up семантика (как у digest_scheduler.is_in_send_window): окно открыто
    от планового времени до конца суток. Защита от повторного срабатывания —
    Redis-ключ ``cabinet:autostart:YYYY-MM-DD``, не само окно. Если воркер упал
    в HH:MM+2мин и поднялся через час — автостарт всё равно отработает (ключа
    ещё нет). На следующие сутки ключ сменится (новая дата) и окно откроется снова.
    """
    if now.tzinfo is None:
        raise ValueError("now должен быть timezone-aware")
    now_utc = now.astimezone(timezone.utc)
    target_minutes = hour_utc * 60 + minute_utc
    current_minutes = now_utc.hour * 60 + now_utc.minute
    # 24*60 = 1440 — следующие сутки уже не «сегодняшний» автостарт.
    return target_minutes <= current_minutes < 24 * 60


def autostart_done_key(now: datetime) -> str:
    """Redis-ключ дедупа автостарта за сегодняшний день (UTC)."""
    if now.tzinfo is None:
        raise ValueError("now должен быть timezone-aware")
    now_utc = now.astimezone(timezone.utc)
    return f"{AUTOSTART_DONE_KEY_PREFIX}{now_utc.strftime('%Y-%m-%d')}"


def _normalize_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Приводит сырой JSONB к контракту с дефолтами (защита от кривых данных)."""
    raw = raw or {}
    return {
        "enabled": bool(raw.get("enabled", DEFAULT_CONFIG["enabled"])),
        "hour_utc": int(raw.get("hour_utc", DEFAULT_CONFIG["hour_utc"])),
        "minute_utc": int(raw.get("minute_utc", DEFAULT_CONFIG["minute_utc"])),
    }


# ====================== I/O ======================


async def read_autostart_config(engine: AsyncEngine) -> dict[str, Any]:
    """Читает конфиг автостарта из system_config. Если строки нет → DEFAULT_CONFIG."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT value FROM system_config WHERE key = :k"),
                {"k": CONFIG_KEY},
            )
        ).first()
    if row is None:
        return dict(DEFAULT_CONFIG)
    raw = row[0]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("cabinet_autostart: кривой JSON в system_config — беру дефолт")
            raw = None
    return _normalize_config(raw)


async def write_autostart_config(engine: AsyncEngine, config: dict[str, Any]) -> None:
    """UPSERT конфига автостарта в system_config (ON CONFLICT по key)."""
    normalized = _normalize_config(config)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO system_config (key, value, description)
                VALUES (:k, CAST(:v AS JSONB), :descr)
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value,
                    updated_at = NOW()
                """
            ),
            {
                "k": CONFIG_KEY,
                "v": json.dumps(normalized),
                "descr": "Автостарт кабинета по расписанию (enable по дате + scan)",
            },
        )


__all__ = [
    "AUTOSTART_DONE_KEY_PREFIX",
    "AUTOSTART_DONE_TTL_SECONDS",
    "CONFIG_KEY",
    "DEFAULT_CONFIG",
    "autostart_done_key",
    "is_in_autostart_window",
    "read_autostart_config",
    "write_autostart_config",
]

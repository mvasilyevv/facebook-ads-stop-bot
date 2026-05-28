# -*- coding: utf-8 -*-
"""Единая точка чтения Redis-ключа observer:runtime.

Контракт ключа (пишет apps/observer_worker/main.py::_publish_runtime_status):
    {
        "worker_status": "scanning" | "idle" | "dispatch" | "paused",
        "status":        "running"  | "paused",   # нормализованное (добавлено writer'ом)
        "active_phase":  str | None,
        "next_scan_at":  ISO-8601 | None,
        "last_successful_scan_at": ISO-8601 | None,
        "updated_at":    ISO-8601,
    }

Маппинг worker_status → status (используется как fallback если поле "status" отсутствует):
    "scanning"  → "running"
    "idle"      → "running"
    "dispatch"  → "running"
    "paused"    → "paused"
    всё остальное → "unknown"

read_observer_runtime() возвращает нормализованный dict:
    {
        "status":                   "running" | "paused" | "unknown",
        "active_phase":             str | None,
        "next_scan_at":             str | None,
        "last_successful_scan_at":  str | None,
        "updated_at":               str | None,
        "raw":                      dict,  # исходный payload as-is (для health_details)
    }

Никогда не бросает исключений — при ошибке возвращает fallback с status="unknown".
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Redis-ключ (единственное место определения — остальной код импортирует отсюда)
OBSERVER_RUNTIME_KEY = "observer:runtime"

# Значения worker_status, при которых воркер считается живым (status="running")
_RUNNING_WORKER_STATUSES = {"scanning", "idle", "dispatch"}


def _normalize_status(payload: dict[str, Any]) -> str:
    """Нормализует статус воркера в простое running/paused/unknown.

    Смотрит сначала на поле "status" (нормализованное, добавлено в новом контракте),
    затем на "worker_status" (детальное, backward-compat для старых писателей).
    """
    # Предпочитаем уже нормализованное поле "status"
    status = payload.get("status")
    if status in {"running", "paused"}:
        return status

    # Fallback: нормализуем из worker_status
    worker_status = payload.get("worker_status")
    if worker_status in _RUNNING_WORKER_STATUSES:
        return "running"
    if worker_status == "paused":
        return "paused"

    return "unknown"


async def read_observer_runtime(redis: Any) -> dict[str, Any]:
    """Читает и нормализует observer:runtime из Redis.

    Args:
        redis: Redis-клиент (redis.asyncio или fakeredis, поддерживает await redis.get(...)).

    Returns:
        Нормализованный dict с гарантированными ключами:
            status, active_phase, next_scan_at, last_successful_scan_at, updated_at, raw.
        Никогда не бросает исключений.
    """
    _fallback: dict[str, Any] = {
        "status": "unknown",
        "active_phase": None,
        "next_scan_at": None,
        "last_successful_scan_at": None,
        "updated_at": None,
        "raw": {},
    }

    if redis is None:
        return _fallback

    # Читаем из Redis
    try:
        raw_bytes = await redis.get(OBSERVER_RUNTIME_KEY)
    except Exception as exc:
        logger.warning("read_observer_runtime: не удалось прочитать из Redis: %s", exc)
        return _fallback

    if raw_bytes is None:
        return _fallback

    # Парсим JSON
    try:
        payload: dict[str, Any] = json.loads(raw_bytes)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("read_observer_runtime: невалидный JSON в observer:runtime: %s", exc)
        return _fallback

    return {
        "status": _normalize_status(payload),
        "active_phase": payload.get("active_phase"),
        "next_scan_at": payload.get("next_scan_at"),
        "last_successful_scan_at": payload.get("last_successful_scan_at"),
        "updated_at": payload.get("updated_at"),
        "raw": payload,
    }

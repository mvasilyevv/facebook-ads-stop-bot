# -*- coding: utf-8 -*-
"""Роутер health_details: агрегированный статус воркеров по Redis heartbeat.

Endpoint:
    GET /health/details — агрегирует worker:heartbeat:* ключи из Redis
                          + содержимое observer:runtime.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter

from apps.api.deps import DepRedis
from apps.api.routers.v1.schemas.health import HealthDetailsResponse, WorkerStatus
from core.observer.runtime import read_observer_runtime

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

# Список воркеров по умолчанию (если EXPECTED_WORKERS не задан в env)
# Имена ДОЛЖНЫ совпадать с ключами worker:heartbeat:<name>, которые пишут воркеры
# (короткие, как в health_watchdog и toggle_executor). Раньше были длинные
# (disable_worker и т.п.) → живые воркеры показывались OFFLINE (5/12 баг).
_DEFAULT_EXPECTED_WORKERS: list[str] = [
    "observer",
    "disable",
    "enable",
    "telegram_poller",
    "meta_api",
    "health_watchdog",
    "cleanup",
    "reconciler",
    "enable_reco",
    "digest_scheduler",
    "creator",
    "creator_recorder",
    "cabinet_scheduler",
]


def _get_expected_workers() -> list[str]:
    """Читает список ожидаемых воркеров из env EXPECTED_WORKERS (CSV).

    Fallback: хардкод из 12 имён.
    """
    env_val = os.environ.get("EXPECTED_WORKERS", "").strip()
    if env_val:
        return [w.strip() for w in env_val.split(",") if w.strip()]
    return list(_DEFAULT_EXPECTED_WORKERS)


def _determine_overall(
    workers: list[WorkerStatus],
    expected: list[str],
) -> Literal["HEALTHY", "DEGRADED", "CRITICAL"]:
    """Определяет общий статус системы по статусам воркеров.

    - observer OFFLINE → CRITICAL
    - любой expected OFFLINE → DEGRADED
    - все expected ONLINE → HEALTHY
    """
    status_map = {w.name: w.status for w in workers}

    if status_map.get("observer") == "OFFLINE":
        return "CRITICAL"

    offline_count = sum(1 for name in expected if status_map.get(name) == "OFFLINE")
    if offline_count > 0:
        return "DEGRADED"

    return "HEALTHY"


@router.get("/health/details", response_model=HealthDetailsResponse)
async def get_health_details(redis: DepRedis) -> HealthDetailsResponse:
    """Агрегирует статусы воркеров по Redis worker:heartbeat:* ключам.

    Использует SCAN MATCH (не KEYS) для безопасного обхода Redis.
    Статус воркера: ONLINE если heartbeat-ключ существует (TTL > 0),
    OFFLINE если ключ протух или не найден.

    overall:
        HEALTHY  — все expected ONLINE
        DEGRADED — >0 OFFLINE
        CRITICAL — observer OFFLINE
    """
    expected_workers = _get_expected_workers()

    # Собираем все heartbeat-ключи через SCAN MATCH
    found: dict[str, dict[str, Any]] = {}
    try:
        async for key in redis.scan_iter(match="worker:heartbeat:*"):
            worker_name = key.removeprefix("worker:heartbeat:")
            # Читаем значение и TTL параллельно
            try:
                raw_value = await redis.get(key)
                ttl = await redis.ttl(key)
            except Exception as exc:
                logger.warning("Не удалось прочитать heartbeat-ключ %s: %s", key, exc)
                continue

            payload: dict[str, Any] | None = None
            last_heartbeat_at: datetime | None = None

            if raw_value:
                try:
                    data = json.loads(raw_value)
                    if isinstance(data, dict):
                        payload = data
                        # Если внутри есть ts/timestamp — парсим
                        ts_raw = data.get("ts") or data.get("timestamp") or data.get("at")
                        if ts_raw:
                            try:
                                last_heartbeat_at = datetime.fromisoformat(str(ts_raw))
                            except (ValueError, TypeError):
                                pass
                    else:
                        # Скаляр: воспринимаем как timestamp
                        try:
                            last_heartbeat_at = datetime.fromisoformat(str(data))
                        except (ValueError, TypeError):
                            pass
                except (json.JSONDecodeError, TypeError):
                    # Не JSON — значение может быть просто ISO-строкой
                    try:
                        last_heartbeat_at = datetime.fromisoformat(raw_value)
                    except (ValueError, TypeError):
                        pass

            found[worker_name] = {
                "ttl": ttl,
                "last_heartbeat_at": last_heartbeat_at,
                "payload": payload,
            }
    except Exception as exc:
        logger.warning("Ошибка SCAN MATCH worker:heartbeat:*: %s", exc)

    # Строим список WorkerStatus для ожидаемых воркеров
    workers: list[WorkerStatus] = []
    for name in expected_workers:
        info = found.get(name)
        if info is not None and info["ttl"] != 0:
            # TTL = -1 означает ключ без TTL (SET без EX) — считаем ONLINE
            status: Literal["ONLINE", "OFFLINE"] = "ONLINE"
            ttl_seconds = info["ttl"] if info["ttl"] > 0 else None
        else:
            status = "OFFLINE"
            ttl_seconds = None
            info = info or {}

        workers.append(
            WorkerStatus(
                name=name,
                status=status,
                last_heartbeat_at=info.get("last_heartbeat_at") if info else None,
                ttl_seconds=ttl_seconds,
                payload=info.get("payload") if info else None,
            )
        )

    # Читаем observer:runtime через единую точку — raw payload отдаём as-is клиенту
    _runtime = await read_observer_runtime(redis)
    observer_runtime: dict[str, Any] | None = _runtime["raw"] if _runtime["raw"] else None

    overall = _determine_overall(workers, expected_workers)

    return HealthDetailsResponse(
        workers=workers,
        observer_runtime=observer_runtime,
        overall=overall,
    )

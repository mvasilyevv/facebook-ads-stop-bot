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
from apps.api.routers.v1.schemas.health import (
    HealthDetailsResponse,
    MetaApiChannelStatus,
    WorkerStatus,
)
from core.observer.runtime import read_observer_runtime

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

# Redis-ключ probe канала Marketing API — пишет health_watchdog (см. контрактный тест
# test_meta_channel_key_contract). Здесь только читаем (роутер не зависит от browser-agent).
META_CHANNEL_HEALTH_KEY = "meta_api:channel:health"

# Список воркеров по умолчанию (если EXPECTED_WORKERS не задан в env)
# Имена ДОЛЖНЫ совпадать с ключами worker:heartbeat:<name>, которые пишут воркеры
# (короткие). disable/enable удалены: отключение/включение рекламы идёт через
# Marketing API (meta_api), отдельных DOM-toggle воркеров больше нет.
_DEFAULT_EXPECTED_WORKERS: list[str] = [
    "observer",
    "telegram_poller",
    "meta_api",
    "health_watchdog",
    "cleanup",
    "reconciler",
    "enable_reco",
    "tracker_aggregator",
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
    meta_channel: MetaApiChannelStatus | None = None,
) -> Literal["HEALTHY", "DEGRADED", "CRITICAL"]:
    """Определяет общий статус системы по статусам воркеров и канала Marketing API.

    - observer OFFLINE → CRITICAL
    - любой expected OFFLINE ИЛИ meta-канал DEGRADED (network-down/протух токен) → DEGRADED
    - всё ONLINE → HEALTHY

    meta-канал UNKNOWN (нет прободера / протух ключ) overall НЕ понижает — отсутствие
    данных не равно отказу (money-сигнал даёт сам watchdog отдельным CRITICAL-алертом).
    """
    status_map = {w.name: w.status for w in workers}

    if status_map.get("observer") == "OFFLINE":
        return "CRITICAL"

    offline_count = sum(1 for name in expected if status_map.get(name) == "OFFLINE")
    if offline_count > 0:
        return "DEGRADED"

    if meta_channel is not None and meta_channel.status == "DEGRADED":
        return "DEGRADED"

    return "HEALTHY"


async def _read_meta_api_channel(redis: DepRedis) -> MetaApiChannelStatus:
    """Читает Redis meta_api:channel:health → статус канала Marketing API.

    Ключ есть + healthy → ONLINE; есть + не healthy → DEGRADED; нет/битый → UNKNOWN.
    Роутер только читает Redis (не зависит от browser-agent) — probe делает health_watchdog.
    """
    try:
        raw = await redis.get(META_CHANNEL_HEALTH_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("не удалось прочитать %s: %s", META_CHANNEL_HEALTH_KEY, exc)
        return MetaApiChannelStatus(status="UNKNOWN")

    if not raw:
        return MetaApiChannelStatus(status="UNKNOWN")

    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return MetaApiChannelStatus(status="UNKNOWN")
    except (json.JSONDecodeError, TypeError):
        return MetaApiChannelStatus(status="UNKNOWN")

    healthy = bool(data.get("healthy", False))
    checked_at: datetime | None = None
    checked_raw = data.get("checked_at")
    if isinstance(checked_raw, str) and checked_raw:
        try:
            checked_at = datetime.fromisoformat(checked_raw)
        except (ValueError, TypeError):
            pass

    return MetaApiChannelStatus(
        status="ONLINE" if healthy else "DEGRADED",
        healthy=healthy,
        probe_ok=bool(data.get("probe_ok", False)),
        detail=str(data.get("detail")) if data.get("detail") is not None else None,
        reason=str(data.get("reason")) if data.get("reason") is not None else None,
        checked_at=checked_at,
    )


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

    # Статус сетевого канала Marketing API (probe пишет health_watchdog в Redis)
    meta_api_channel = await _read_meta_api_channel(redis)

    overall = _determine_overall(workers, expected_workers, meta_api_channel)

    return HealthDetailsResponse(
        workers=workers,
        observer_runtime=observer_runtime,
        meta_api_channel=meta_api_channel,
        overall=overall,
    )

# -*- coding: utf-8 -*-
"""Health endpoints для k8s/мониторинга.

- GET /healthz — простой liveness, всегда 200 (без БД).
- GET /readyz  — readiness: проверяет Postgres (SELECT 1) и Redis (PING).
                 Кэш 5 секунд, чтобы не бомбить инфру при частом polling'е.
- GET /metrics — Prometheus exposition.

В лог пишем только аномалии (не успешный readyz), чтобы k8s probe не спамил.
"""

from __future__ import annotations

import logging
import time
from typing import Literal

from fastapi import APIRouter, Depends, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from redis.asyncio import Redis  # type: ignore[import-not-found]
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.api.deps import get_engine, get_redis
from apps.api.routers.v1.health_details import get_health_details
from core.observer.queries import load_scanning_enabled

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

_READYZ_TTL_SECONDS: float = 5.0
_readyz_cache: dict[str, float | bool] = {"checked_at": 0.0, "ready": False}


class SystemReadinessResponse(BaseModel):
    """Строгая готовность money-критичного контура, не k8s probe."""

    ready: bool
    infrastructure_ready: bool
    overall: Literal["HEALTHY", "DEGRADED", "CRITICAL"] | None = None
    workers_online: int = 0
    workers_expected: int = 0
    observer_runtime_status: str | None = None
    scanning_enabled: bool | None = None
    meta_api_channel_status: Literal["ONLINE", "DEGRADED", "UNKNOWN"] | None = None
    blockers: list[str] = Field(default_factory=list)


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe для k8s: всегда 200, не лезет в БД/Redis."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(
    response: Response,
    engine: AsyncEngine = Depends(get_engine),
    redis: Redis = Depends(get_redis),
) -> dict[str, object]:
    """Readiness probe: Postgres + Redis. Кэш 5 секунд, чтобы не бомбить инфру."""
    now = time.monotonic()
    cache_age = now - float(_readyz_cache["checked_at"])
    if cache_age < _READYZ_TTL_SECONDS and _readyz_cache["checked_at"] > 0:
        ready = bool(_readyz_cache["ready"])
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"ready": ready, "cached": True}

    pg_ok = await _check_postgres(engine)
    redis_ok = await _check_redis(redis)
    ready = pg_ok and redis_ok

    _readyz_cache["checked_at"] = now
    _readyz_cache["ready"] = ready

    if not ready:
        logger.warning("readyz: postgres=%s redis=%s", pg_ok, redis_ok)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"ready": ready, "postgres": pg_ok, "redis": redis_ok, "cached": False}


@router.get(
    "/system-readyz",
    response_model=SystemReadinessResponse,
    responses={503: {"model": SystemReadinessResponse}},
)
async def system_readyz(
    response: Response,
    engine: AsyncEngine = Depends(get_engine),
    redis: Redis = Depends(get_redis),
) -> SystemReadinessResponse:
    """Готовность бизнес-контура auto-stop.

    В отличие от ``/readyz`` проверяет не только инфраструктуру API, но и все
    ожидаемые heartbeat, живой runtime observer, Meta API probe и глобальный
    флаг сканирования. Используется оператором и post-deploy проверками; k8s
    liveness/readiness намеренно остаются независимыми от состояния воркеров.
    """
    pg_ok = await _check_postgres(engine)
    redis_ok = await _check_redis(redis)
    infrastructure_ready = pg_ok and redis_ok
    blockers: list[str] = []

    if not pg_ok:
        blockers.append("postgres_unavailable")
    if not redis_ok:
        blockers.append("redis_unavailable")

    overall: Literal["HEALTHY", "DEGRADED", "CRITICAL"] | None = None
    workers_online = 0
    workers_expected = 0
    observer_runtime_status: str | None = None
    meta_api_channel_status: Literal["ONLINE", "DEGRADED", "UNKNOWN"] | None = None

    if redis_ok:
        details = await get_health_details(redis)
        overall = details.overall
        workers_expected = len(details.workers)
        workers_online = sum(worker.status == "ONLINE" for worker in details.workers)
        offline_workers = [worker.name for worker in details.workers if worker.status == "OFFLINE"]
        if offline_workers:
            blockers.append(f"offline_workers:{','.join(offline_workers)}")

        runtime = details.observer_runtime or {}
        runtime_status = runtime.get("status")
        observer_runtime_status = runtime_status if isinstance(runtime_status, str) else None
        if observer_runtime_status != "running":
            blockers.append(
                "observer_runtime_missing"
                if observer_runtime_status is None
                else f"observer_runtime_{observer_runtime_status}"
            )

        if details.meta_api_channel is not None:
            meta_api_channel_status = details.meta_api_channel.status
        if meta_api_channel_status != "ONLINE":
            blockers.append(
                "meta_api_channel_unknown"
                if meta_api_channel_status in {None, "UNKNOWN"}
                else "meta_api_channel_degraded"
            )

    scanning_enabled: bool | None = None
    if pg_ok:
        try:
            scanning_enabled = await load_scanning_enabled(engine)
        except Exception as exc:  # noqa: BLE001
            logger.warning("system-readyz: не удалось прочитать observer_config: %s", exc)
            blockers.append("observer_config_unavailable")
        else:
            if not scanning_enabled:
                blockers.append("scanning_paused")

    is_ready = infrastructure_ready and not blockers
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return SystemReadinessResponse(
        ready=is_ready,
        infrastructure_ready=infrastructure_ready,
        overall=overall,
        workers_online=workers_online,
        workers_expected=workers_expected,
        observer_runtime_status=observer_runtime_status,
        scanning_enabled=scanning_enabled,
        meta_api_channel_status=meta_api_channel_status,
        blockers=blockers,
    )


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus exposition format. Метрики собираются в middleware."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def _check_postgres(engine: AsyncEngine) -> bool:
    """SELECT 1 на короткое соединение — True если Postgres отвечает."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("readyz: Postgres недоступен: %s", exc)
        return False


async def _check_redis(redis: Redis) -> bool:
    """PING на Redis — True если живой."""
    try:
        await redis.ping()
        return True
    except Exception as exc:
        logger.warning("readyz: Redis недоступен: %s", exc)
        return False


def reset_readyz_cache() -> None:
    """Сбросить TTL-кэш /readyz. Нужно тестам, не используется в проде."""
    _readyz_cache["checked_at"] = 0.0
    _readyz_cache["ready"] = False

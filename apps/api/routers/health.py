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

from fastapi import APIRouter, Depends, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.asyncio import Redis  # type: ignore[import-not-found]
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.api.deps import get_engine, get_redis

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

_READYZ_TTL_SECONDS: float = 5.0
_readyz_cache: dict[str, float | bool] = {"checked_at": 0.0, "ready": False}


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

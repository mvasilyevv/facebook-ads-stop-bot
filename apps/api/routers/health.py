# -*- coding: utf-8 -*-
"""Health endpoints for probes and the PostgreSQL control plane.

- GET /healthz — простой liveness, всегда 200 (без БД).
- GET /readyz  — readiness: Postgres обязателен, Redis отражается как optional cache.
                 Кэш 5 секунд, чтобы не бомбить инфру при частом polling'е.
- GET /system-readyz — durable observer/task evidence from PostgreSQL only.
- GET /metrics — Prometheus exposition.

В лог пишем только аномалии (не успешный readyz), чтобы platform probe не спамил.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from redis.asyncio import Redis  # type: ignore[import-not-found]
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.api.deps import get_engine, get_redis
from core.observer.accounts import resolve_configured_ad_account_ids
from core.operator.queries import fetch_operator_scan_state

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

_READYZ_TTL_SECONDS: float = 5.0
_SYSTEM_ACTIVITY_MAX_AGE_SECONDS = 90
_readyz_cache: dict[str, float | bool] = {
    "checked_at": 0.0,
    "ready": False,
    "postgres": False,
    "redis": False,
}


class SystemReadinessResponse(BaseModel):
    """Строгая готовность money-критичного контура, не простой liveness probe."""

    ready: bool
    infrastructure_ready: bool
    overall: Literal["HEALTHY", "DEGRADED", "CRITICAL"]
    actors_active: int = 0
    actors_expected: int = 0
    scanning_enabled: bool | None = None
    last_scan_at: datetime | None = None
    last_activity_at: datetime | None = None
    stale_money_tasks: int = 0
    expired_money_tasks: int = 0
    blockers: list[str] = Field(default_factory=list)
    degraded: list[str] = Field(default_factory=list)


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe платформы: всегда 200, не лезет в БД/Redis."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(
    response: Response,
    engine: AsyncEngine = Depends(get_engine),
    redis: Redis = Depends(get_redis),
) -> dict[str, object]:
    """Readiness probe: PostgreSQL is required; Redis is optional degraded state."""
    now = time.monotonic()
    cache_age = now - float(_readyz_cache["checked_at"])
    if cache_age < _READYZ_TTL_SECONDS and _readyz_cache["checked_at"] > 0:
        ready = bool(_readyz_cache["ready"])
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        redis_ok = bool(_readyz_cache["redis"])
        return {
            "ready": ready,
            "postgres": bool(_readyz_cache["postgres"]),
            "redis": redis_ok,
            "degraded": [] if redis_ok else ["redis_unavailable"],
            "cached": True,
        }

    pg_ok = await _check_postgres(engine)
    redis_ok = await _check_redis(redis)
    # Durable control and notification state is in PostgreSQL.  Redis is a
    # disposable cache/accelerator and must not take the API or
    # control plane out of readiness when it is unavailable.
    ready = pg_ok

    _readyz_cache["checked_at"] = now
    _readyz_cache["ready"] = ready
    _readyz_cache["postgres"] = pg_ok
    _readyz_cache["redis"] = redis_ok

    if not ready:
        logger.warning("readyz: required postgres is unavailable")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif not redis_ok:
        logger.warning("readyz: optional Redis is unavailable; serving degraded")

    return {
        "ready": ready,
        "postgres": pg_ok,
        "redis": redis_ok,
        "degraded": [] if redis_ok else ["redis_unavailable"],
        "cached": False,
    }


@router.get(
    "/system-readyz",
    response_model=SystemReadinessResponse,
    responses={503: {"model": SystemReadinessResponse}},
)
async def system_readyz(
    response: Response,
    engine: AsyncEngine = Depends(get_engine),
) -> SystemReadinessResponse:
    """Готовность бизнес-контура auto-stop.

    В отличие от ``/readyz`` проверяет persisted scan/actor/task lifecycle.
    Redis и process-local heartbeats сюда намеренно не входят: process liveness
    проверяют Prometheus/blackbox, а этот endpoint не должен расходиться с
    PostgreSQL control plane после restart или single-slot redeploy.
    """
    pg_ok = await _check_postgres(engine)
    blockers: list[str] = []
    degraded: list[str] = []
    if not pg_ok:
        blockers.append("postgres_unavailable")
    scanning_enabled: bool | None = None
    actors_active = 0
    actors_expected = 0
    last_scan_at: datetime | None = None
    last_activity_at: datetime | None = None
    stale_money_tasks = 0
    expired_money_tasks = 0

    if pg_ok:
        try:
            now = datetime.now(UTC)
            scan = await fetch_operator_scan_state(engine)
            expected_accounts = await resolve_configured_ad_account_ids(engine)
            stale_money_tasks, expired_money_tasks = await _load_money_task_failures(engine)
        except Exception as exc:  # noqa: BLE001
            logger.warning("system-readyz: durable evidence unavailable: %s", exc)
            blockers.append("control_plane_evidence_unavailable")
        else:
            scanning_enabled = scan.get("enabled")
            last_scan_at = scan.get("last_scan_at")
            actors_expected = len(expected_accounts)
            actors_by_account = {
                str(actor.get("ad_account_id")): actor for actor in scan.get("actors", [])
            }
            activities = [
                activity
                for actor in actors_by_account.values()
                if (activity := _actor_last_activity(actor)) is not None
            ]
            last_activity_at = max(activities, default=None)
            stale_accounts: list[str] = []
            for account_id in expected_accounts:
                actor = actors_by_account.get(account_id)
                activity = _actor_last_activity(actor) if actor else None
                error = actor.get("error") if actor else None
                if (
                    activity is not None
                    and (now - activity).total_seconds() <= _SYSTEM_ACTIVITY_MAX_AGE_SECONDS
                    and not error
                ):
                    actors_active += 1
                else:
                    stale_accounts.append(account_id)

            if not scanning_enabled:
                blockers.append("scanning_paused")
            elif not expected_accounts:
                blockers.append("scan_accounts_missing")
            elif stale_accounts:
                blockers.append(f"stale_cabinet_actors:{','.join(stale_accounts)}")

            if scanning_enabled and last_scan_at is None:
                blockers.append("scan_snapshot_missing")
            elif scanning_enabled and last_scan_at is not None:
                scan_age = max(0, int((now - last_scan_at).total_seconds()))
                if scan_age > _SYSTEM_ACTIVITY_MAX_AGE_SECONDS:
                    blockers.append(f"scan_snapshot_stale:{scan_age}")

            if stale_money_tasks:
                blockers.append(f"stale_money_tasks:{stale_money_tasks}")
            if expired_money_tasks:
                blockers.append(f"expired_money_tasks:{expired_money_tasks}")

            degraded.extend(
                f"cabinet_actor_error:{account_id}"
                for account_id, actor in actors_by_account.items()
                if actor.get("error") and account_id not in expected_accounts
            )

    is_ready = pg_ok and not blockers
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    overall: Literal["HEALTHY", "DEGRADED", "CRITICAL"] = (
        "CRITICAL" if blockers else "DEGRADED" if degraded else "HEALTHY"
    )

    return SystemReadinessResponse(
        ready=is_ready,
        infrastructure_ready=pg_ok,
        overall=overall,
        actors_active=actors_active,
        actors_expected=actors_expected,
        scanning_enabled=scanning_enabled,
        last_scan_at=last_scan_at,
        last_activity_at=last_activity_at,
        stale_money_tasks=stale_money_tasks,
        expired_money_tasks=expired_money_tasks,
        blockers=blockers,
        degraded=degraded,
    )


def _actor_last_activity(actor: dict[str, object] | None) -> datetime | None:
    if actor is None:
        return None
    values = [
        value
        for key in ("last_progress_at", "last_snapshot_at")
        if isinstance((value := actor.get(key)), datetime)
    ]
    return max(values, default=None)


async def _load_money_task_failures(engine: AsyncEngine) -> tuple[int, int]:
    """Return fenced money tasks whose durable lifecycle already missed safety bounds."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT
                      COUNT(*) FILTER (
                        WHERE status = 'running'
                          AND (lease_expires_at IS NULL OR lease_expires_at <= NOW())
                      ) AS stale_running,
                      COUNT(*) FILTER (
                        WHERE status IN ('pending', 'retrying')
                          AND deadline_at IS NOT NULL
                          AND deadline_at <= NOW()
                      ) AS expired_pending
                    FROM task_queue
                    WHERE lane = 'money'
                    """
                )
            )
        ).one()
    return int(row.stale_running or 0), int(row.expired_pending or 0)


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
    _readyz_cache["postgres"] = False
    _readyz_cache["redis"] = False

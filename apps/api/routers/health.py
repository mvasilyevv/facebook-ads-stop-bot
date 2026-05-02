# -*- coding: utf-8 -*-
"""Health-check эндпоинты для liveness/readiness и health_watchdog."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

import grpc
import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_db
from core.config import get_settings
from core.domain import DisableTaskStatus, EnableTaskStatus
from core.models import (
    AdSnapshot,
    DisableTask,
    EnableTask,
    ObserverSettings,
    TelegramSettings,
    WorkerHeartbeat,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["health"])

# Пороги свежести heartbeat (секунды)
_THRESHOLD_FAST = 60.0  # observer, telegram_poller
_THRESHOLD_SLOW = 120.0  # disable, enable, enable_recommendation, health_watchdog

# Timeout для внешних проверок
_EXTERNAL_CHECK_TIMEOUT = 5.0

# Известные воркеры и их пороги
_WORKERS: dict[str, float] = {
    "observer": _THRESHOLD_FAST,
    "telegram_poller": _THRESHOLD_FAST,
    "disable": _THRESHOLD_SLOW,
    "enable": _THRESHOLD_SLOW,
    "enable_recommendation": _THRESHOLD_SLOW,
    "health_watchdog": _THRESHOLD_SLOW,
}


# --- Pydantic-модели ---


class DbHealth(BaseModel):
    """Состояние подключения к базе данных."""

    model_config = ConfigDict(from_attributes=True)

    healthy: bool
    latency_ms: int | None = None


class WorkerHealth(BaseModel):
    """Состояние отдельного воркера по heartbeat."""

    model_config = ConfigDict(from_attributes=True)

    healthy: bool
    last_heartbeat_at: datetime | None = None
    heartbeat_age_seconds: float | None = None


class ExternalServiceHealth(BaseModel):
    """Состояние внешнего сервиса (browser-agent, Vision)."""

    model_config = ConfigDict(from_attributes=True)

    healthy: bool
    error: str | None = None


class QueueCounts(BaseModel):
    """Размеры очередей задач."""

    model_config = ConfigDict(from_attributes=True)

    disable_pending: int = 0
    disable_running: int = 0
    enable_pending: int = 0
    enable_running: int = 0


class LastScanInfo(BaseModel):
    """Информация о последнем успешном скане."""

    model_config = ConfigDict(from_attributes=True)

    at: datetime | None = None
    age_seconds: float | None = None


class HealthDetails(BaseModel):
    """Полный readiness-ответ со статусами всех компонентов."""

    model_config = ConfigDict(from_attributes=True)

    overall_healthy: bool
    checked_at: datetime
    database: DbHealth
    workers: dict[str, WorkerHealth]
    browser_agent: ExternalServiceHealth
    vision: ExternalServiceHealth
    queues: QueueCounts
    last_successful_scan: LastScanInfo
    # TODO: поле появится после Wave 2 (модальные диалоги). Пока всегда 0.
    unknown_modals_last_hour: int = 0


# --- Вспомогательные функции ---


def _worker_health(heartbeat_at: datetime | None, threshold_seconds: float) -> WorkerHealth:
    """Вычисляет статус воркера по времени последнего heartbeat."""
    if heartbeat_at is None:
        return WorkerHealth(healthy=False, last_heartbeat_at=None, heartbeat_age_seconds=None)

    now = datetime.now(UTC)
    # Приводим к aware если нет tzinfo
    hb = heartbeat_at if heartbeat_at.tzinfo else heartbeat_at.replace(tzinfo=UTC)
    age = (now - hb).total_seconds()
    return WorkerHealth(
        healthy=age < threshold_seconds,
        last_heartbeat_at=hb,
        heartbeat_age_seconds=round(age, 1),
    )


async def _check_browser_agent() -> ExternalServiceHealth:
    """Проверяет доступность browser-agent gRPC-сервиса через channel_ready."""
    host = "localhost"
    port = 50051
    try:
        channel = grpc.aio.insecure_channel(f"{host}:{port}")
        try:
            await asyncio.wait_for(channel.channel_ready(), timeout=_EXTERNAL_CHECK_TIMEOUT)
        finally:
            await channel.close()
        return ExternalServiceHealth(healthy=True)
    except asyncio.TimeoutError:
        return ExternalServiceHealth(
            healthy=False, error=f"Timeout {_EXTERNAL_CHECK_TIMEOUT}s при подключении к gRPC"
        )
    except Exception as exc:
        return ExternalServiceHealth(healthy=False, error=str(exc)[:200])


async def _check_vision(api_url: str, x_token: str) -> ExternalServiceHealth:
    """Проверяет доступность Vision anti-detect браузера через GET /list."""
    try:
        async with httpx.AsyncClient(timeout=_EXTERNAL_CHECK_TIMEOUT) as client:
            resp = await client.get(
                f"{api_url.rstrip('/')}/list",
                headers={"X-Token": x_token} if x_token else {},
            )
            if resp.status_code < 500:
                return ExternalServiceHealth(healthy=True)
            return ExternalServiceHealth(
                healthy=False, error=f"Vision вернул HTTP {resp.status_code}"
            )
    except httpx.TimeoutException:
        return ExternalServiceHealth(
            healthy=False, error=f"Timeout {_EXTERNAL_CHECK_TIMEOUT}s при запросе к Vision"
        )
    except Exception as exc:
        return ExternalServiceHealth(healthy=False, error=str(exc)[:200])


# --- Эндпоинты ---


@router.get("/health")
async def health_liveness(db: AsyncSession = Depends(get_db)):
    """Liveness-проверка: БД доступна → {"status": "ok"}.

    Не требует авторизации. Возвращает 503 при недоступной БД.
    """
    from fastapi import HTTPException

    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("Liveness БД недоступна: %s", exc)
        raise HTTPException(status_code=503, detail="База данных недоступна") from exc
    return {"status": "ok"}


async def collect_health_details(db: AsyncSession) -> HealthDetails:
    """Собирает полный health-статус всех компонентов системы.

    Используется как в HTTP-эндпоинте /api/health/details,
    так и в health_watchdog для периодических проверок.
    """
    checked_at = datetime.now(UTC)

    # --- Проверка БД ---
    t0 = time.monotonic()
    db_healthy = True
    try:
        await db.execute(text("SELECT 1"))
        latency_ms = int((time.monotonic() - t0) * 1000)
    except Exception as exc:
        logger.warning("Health БД ошибка: %s", exc)
        db_healthy = False
        latency_ms = None

    database = DbHealth(healthy=db_healthy, latency_ms=latency_ms)

    # --- Heartbeats воркеров ---
    observer_heartbeat: datetime | None = None
    poller_heartbeat: datetime | None = None

    if db_healthy:
        try:
            obs_row = await db.scalar(
                select(ObserverSettings).where(ObserverSettings.singleton_key == "default")
            )
            if obs_row:
                observer_heartbeat = obs_row.worker_heartbeat_at
        except Exception as exc:
            logger.warning("Health: не удалось прочитать ObserverSettings: %s", exc)

        try:
            tg_row = await db.scalar(
                select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
            )
            if tg_row:
                poller_heartbeat = tg_row.poller_heartbeat_at
        except Exception as exc:
            logger.warning("Health: не удалось прочитать TelegramSettings: %s", exc)

    workers: dict[str, WorkerHealth] = {
        "observer": _worker_health(observer_heartbeat, _WORKERS["observer"]),
        "telegram_poller": _worker_health(poller_heartbeat, _WORKERS["telegram_poller"]),
        # Воркеры с heartbeat в таблице worker_heartbeats — читаем ниже
        "disable": WorkerHealth(healthy=False),
        "enable": WorkerHealth(healthy=False),
        "enable_recommendation": WorkerHealth(healthy=False),
        "health_watchdog": WorkerHealth(healthy=False),
    }

    if db_healthy:
        try:
            hb_rows = (
                (
                    await db.execute(
                        select(WorkerHeartbeat).where(
                            WorkerHeartbeat.worker_name.in_(
                                ["disable", "enable", "enable_recommendation", "health_watchdog"]
                            )
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in hb_rows:
                if row.worker_name in _WORKERS:
                    workers[row.worker_name] = _worker_health(
                        row.last_heartbeat_at, _WORKERS[row.worker_name]
                    )
        except Exception as exc:
            logger.warning("Health: не удалось прочитать worker_heartbeats: %s", exc)

    # --- Внешние сервисы (параллельно) ---
    cfg = get_settings()
    browser_agent_task = asyncio.create_task(_check_browser_agent())
    vision_task = asyncio.create_task(_check_vision(cfg.vision_api_url, cfg.vision_x_token))
    browser_agent, vision = await asyncio.gather(browser_agent_task, vision_task)

    # --- Очереди задач ---
    queues = QueueCounts()
    if db_healthy:
        try:
            disable_counts = (
                await db.execute(
                    select(DisableTask.status, func.count(DisableTask.id))
                    .where(
                        DisableTask.status.in_(
                            [DisableTaskStatus.PENDING, DisableTaskStatus.RUNNING]
                        )
                    )
                    .group_by(DisableTask.status)
                )
            ).all()
            for status, cnt in disable_counts:
                if status == DisableTaskStatus.PENDING:
                    queues.disable_pending = cnt
                elif status == DisableTaskStatus.RUNNING:
                    queues.disable_running = cnt

            enable_counts = (
                await db.execute(
                    select(EnableTask.status, func.count(EnableTask.id))
                    .where(
                        EnableTask.status.in_([EnableTaskStatus.PENDING, EnableTaskStatus.RUNNING])
                    )
                    .group_by(EnableTask.status)
                )
            ).all()
            for status, cnt in enable_counts:
                if status == EnableTaskStatus.PENDING:
                    queues.enable_pending = cnt
                elif status == EnableTaskStatus.RUNNING:
                    queues.enable_running = cnt
        except Exception as exc:
            logger.warning("Health: не удалось прочитать очереди: %s", exc)

    # --- Последний успешный скан ---
    last_scan = LastScanInfo()
    if db_healthy:
        try:
            max_observed = await db.scalar(select(func.max(AdSnapshot.last_observed_at)))
            if max_observed:
                ts = max_observed if max_observed.tzinfo else max_observed.replace(tzinfo=UTC)
                age = (checked_at - ts).total_seconds()
                last_scan = LastScanInfo(at=ts, age_seconds=round(age, 1))
        except Exception as exc:
            logger.warning("Health: не удалось прочитать last_observed_at: %s", exc)

    # --- overall_healthy ---
    # Считаем только observer и telegram_poller как критичные воркеры.
    # disable/enable/enable_recommendation — вспомогательные, не блокируют overall.
    critical_workers_healthy = all(workers[w].healthy for w in ("observer", "telegram_poller"))
    overall_healthy = (
        db_healthy and critical_workers_healthy and browser_agent.healthy and vision.healthy
    )

    return HealthDetails(
        overall_healthy=overall_healthy,
        checked_at=checked_at,
        database=database,
        workers=workers,
        browser_agent=browser_agent,
        vision=vision,
        queues=queues,
        last_successful_scan=last_scan,
    )


@router.get("/health/details", response_model=HealthDetails)
async def health_details(db: AsyncSession = Depends(get_db)) -> HealthDetails:
    """Readiness-проверка всех компонентов системы.

    Всегда возвращает HTTP 200. Если что-то не в порядке — overall_healthy=false.
    Не требует авторизации.
    """
    return await collect_health_details(db)

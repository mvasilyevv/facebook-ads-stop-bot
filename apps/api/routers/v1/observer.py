# -*- coding: utf-8 -*-
"""Роутер observer: статус, история сканов, кабинетные сутки, рестарт.

Endpoints:
    GET  /observer/status                — статус observer-воркера из Redis
    GET  /observer/scan-runs             — история сканов из scan_runs (partitioned)
    POST /observer/start-new-cabinet-day — архив за вчера + pubsub
    POST /observer/restart               — сигнал рестарта observer-воркеру
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from redis.asyncio import Redis  # type: ignore[import-not-found]
from sqlalchemy import func, select

from apps.api.deps import DepEngine, DepRedis
from apps.api.routers.v1.schemas.observer import (
    ObserverStatusResponse,
    RestartSignalResponse,
    ScanRunRow,
    ScanRunsResponse,
    StartCabinetDayResponse,
)
from apps.api.utils.partition import default_window
from core.models.observer.cabinet_day_archive import CabinetDayArchive
from core.models.observer.scan_run import ScanRun
from core.observer.runtime import read_observer_runtime

logger = logging.getLogger(__name__)

router = APIRouter(tags=["observer"])

# Максимально допустимый limit для scan-runs
_MAX_SCAN_RUNS_LIMIT = 200

# Redis-каналы и ключи
_RESTART_OBSERVER_CHANNEL = "fb_agent:worker:restart:observer"
_CABINET_DAY_CHANNEL = "fb_agent:observer:cabinet_day"


# ─────────────────────────────── GET /observer/status ────────────────────────


@router.get("/observer/status", response_model=ObserverStatusResponse)
async def get_observer_status(redis: DepRedis, engine: DepEngine) -> ObserverStatusResponse:
    """Возвращает статус observer-воркера из Redis ключа observer:runtime.

    Если ключ отсутствует — возвращает {status: unknown, last_scan_at: null, ...}.
    Никогда не падает с 5xx.
    Использует read_observer_runtime() — единственную точку чтения контракта.
    Дополнительно из scan_runs считает scans_today и outcome последнего скана.
    """
    runtime = await read_observer_runtime(redis)

    # last_scan_at берём из last_successful_scan_at (детальное поле воркера)
    last_scan_at: datetime | None = None
    last_scan_at_raw = runtime.get("last_successful_scan_at")
    if last_scan_at_raw:
        try:
            last_scan_at = datetime.fromisoformat(last_scan_at_raw)
        except (ValueError, TypeError):
            pass

    # interval_seconds не пишется в observer:runtime, берём из raw если есть
    raw = runtime.get("raw", {})
    interval_seconds = raw.get("interval_seconds")

    # extra — всё из raw кроме полей с известным маппингом
    known = {
        "status",
        "worker_status",
        "active_phase",
        "next_scan_at",
        "last_successful_scan_at",
        "updated_at",
        "interval_seconds",
    }
    extra: dict[str, Any] = {k: v for k, v in raw.items() if k not in known}
    # Кладём детальные поля воркера в extra для прозрачности
    if runtime.get("active_phase") is not None:
        extra["active_phase"] = runtime["active_phase"]
    if runtime.get("next_scan_at") is not None:
        extra["next_scan_at"] = runtime["next_scan_at"]
    if runtime.get("updated_at") is not None:
        extra["updated_at"] = runtime["updated_at"]
    if raw.get("worker_status") is not None:
        extra["worker_status"] = raw["worker_status"]

    # Кол-во сканов за сегодня (UTC) + outcome последнего — из scan_runs.
    # WHERE по started_at даёт partition pruning. Не роняем эндпоинт при сбое БД.
    scans_today = 0
    last_scan_outcome: str | None = None
    try:
        today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        async with engine.connect() as conn:
            scans_today = int(
                await conn.scalar(
                    select(func.count(ScanRun.id)).where(ScanRun.started_at >= today_start)
                )
                or 0
            )
            last_scan_outcome = await conn.scalar(
                select(ScanRun.outcome)
                .where(ScanRun.started_at >= today_start)
                .order_by(ScanRun.started_at.desc())
                .limit(1)
            )
    except Exception:
        logger.exception("get_observer_status: не смог посчитать scans_today")

    return ObserverStatusResponse(
        status=runtime["status"],
        last_scan_at=last_scan_at,
        last_scan_outcome=last_scan_outcome,
        scans_today=scans_today,
        interval_seconds=interval_seconds,
        extra=extra,
    )


# ─────────────────────────────── GET /observer/scan-runs ─────────────────────


@router.get("/observer/scan-runs", response_model=ScanRunsResponse)
async def list_scan_runs(
    engine: DepEngine,
    limit: int = Query(default=50, ge=1, le=_MAX_SCAN_RUNS_LIMIT),
    filter: str = Query(default="all", description="all | errors | slow | with_alerts"),
    from_iso: str | None = Query(default=None, description="ISO-8601 начало окна"),
    to_iso: str | None = Query(default=None, description="ISO-8601 конец окна"),
) -> ScanRunsResponse:
    """Возвращает историю scan_runs с обязательным оконным фильтром по started_at.

    Partition pruning требует явного WHERE по started_at.
    Если from_iso/to_iso не переданы — дефолт last 7 days.
    limit автоматически ограничен до 200.
    """
    # Cap limit на случай если Query-валидация обошлась через dependency_overrides
    limit = min(limit, _MAX_SCAN_RUNS_LIMIT)

    # Временное окно (partition pruning)
    if from_iso or to_iso:
        try:
            from_dt = datetime.fromisoformat(from_iso) if from_iso else default_window()[0]
            to_dt = datetime.fromisoformat(to_iso) if to_iso else datetime.now(UTC)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=f"Неверный формат даты: {exc}") from exc
    else:
        from_dt, to_dt = default_window()

    stmt = (
        select(ScanRun)
        .where(ScanRun.started_at >= from_dt)
        .where(ScanRun.started_at <= to_dt)
        .order_by(ScanRun.started_at.desc())
        .limit(limit)
    )

    if filter == "errors":
        stmt = stmt.where(ScanRun.outcome == "error")
    elif filter == "slow":
        stmt = stmt.where(ScanRun.duration_ms > 30_000)
    elif filter == "with_alerts":
        stmt = stmt.where(
            (func.coalesce(ScanRun.alerts_warning, 0) + func.coalesce(ScanRun.alerts_stop, 0)) > 0
        )
    # filter == "all" — без доп условий

    async with engine.connect() as conn:
        result = await conn.execute(stmt)
        rows = result.fetchall()

    run_items = [
        ScanRunRow(
            id=row.id,
            scan_id=row.scan_id,
            started_at=row.started_at,
            finished_at=row.finished_at,
            duration_ms=row.duration_ms,
            outcome=row.outcome,
            alerts_warning=row.alerts_warning,
            alerts_stop=row.alerts_stop,
            # metrics_inserted отсутствует в ScanRun — возвращаем None
            metrics_inserted=None,
            error_message=row.error_message,
            ad_account_id=row.ad_account_id,
        )
        for row in rows
    ]

    return ScanRunsResponse(runs=run_items, total=len(run_items))


# ─────────────────── POST /observer/start-new-cabinet-day ────────────────────


@router.post("/observer/start-new-cabinet-day", response_model=StartCabinetDayResponse)
async def start_new_cabinet_day(engine: DepEngine, redis: DepRedis) -> StartCabinetDayResponse:
    """Архивирует агрегаты за вчера и публикует событие new_cabinet_day.

    Логика:
    1. Агрегируем метрики за прошлые сутки (SUM spend/deposits/leads) из scan_runs.
    2. INSERT в cabinet_day_archives.
    3. Публикуем {event: new_cabinet_day, ...} в Redis канал fb_agent:observer:cabinet_day.

    observer_worker подписан на этот канал (main.py::_on_cabinet_day) и делает
    форс-рескан нового дня. Архив (шаг 2) здесь — единственный источник истины:
    observer его НЕ дублирует.
    """
    now = datetime.now(UTC)
    # Вчерашний день (UTC)
    from datetime import timedelta

    yesterday_start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    archived_date = yesterday_start.date().isoformat()

    # Агрегируем метрики за прошлые сутки из scan_runs (partition pruning по started_at)
    agg_stmt = select(
        func.sum(ScanRun.alerts_warning).label("total_warns"),
        func.sum(ScanRun.alerts_stop).label("total_stops"),
        func.count(ScanRun.id).label("scan_count"),
    ).where(
        ScanRun.started_at >= yesterday_start,
        ScanRun.started_at < yesterday_end,
    )

    async with engine.begin() as conn:
        agg_result = (await conn.execute(agg_stmt)).one()

        raw_aggregate = {
            "alerts_warning": int(agg_result.total_warns or 0),
            "alerts_stop": int(agg_result.total_stops or 0),
            "scan_count": int(agg_result.scan_count or 0),
            "archived_by": "api",
        }

        # INSERT snapshot в cabinet_day_archives
        insert_stmt = CabinetDayArchive.__table__.insert().values(
            started_at=yesterday_start,
            ended_at=yesterday_end,
            reset_detected_at=now,
            raw_aggregate=raw_aggregate,
        )
        await conn.execute(insert_stmt)

    # Публикуем событие в Redis
    event_payload = json.dumps(
        {
            "event": "new_cabinet_day",
            "requested_at": now.isoformat(),
            "archived_date": archived_date,
        }
    )
    try:
        await redis.publish(_CABINET_DAY_CHANNEL, event_payload)
    except Exception as exc:
        logger.warning("Не удалось опубликовать new_cabinet_day в Redis: %s", exc)

    logger.info("Cabinet day archived: %s", archived_date)
    return StartCabinetDayResponse(status="started", archived_date=archived_date)


# ─────────────────────────── POST /observer/restart ──────────────────────────


async def _publish_restart_signal(redis: Redis, channel: str) -> RestartSignalResponse:
    """Публикует сигнал рестарта в Redis-канал.

    Возвращает 503 если Redis недоступен.
    """
    payload = json.dumps({"requested_by": "api", "ts": datetime.now(UTC).isoformat()})
    try:
        await redis.publish(channel, payload)
    except Exception as exc:
        logger.error("Redis недоступен при отправке restart-сигнала (%s): %s", channel, exc)
        raise HTTPException(status_code=503, detail=f"Redis недоступен: {exc}") from exc

    logger.info("Restart-сигнал отправлен в канал: %s", channel)
    return RestartSignalResponse(status="signal_sent", channel=channel)


@router.post("/observer/restart", response_model=RestartSignalResponse)
async def restart_observer(redis: DepRedis) -> RestartSignalResponse:
    """Публикует сигнал рестарта observer-воркера в Redis.

    observer_worker подписан на канал fb_agent:worker:restart:observer
    (main.py::_on_restart) и выполняет graceful stop по этому событию.
    Если Redis недоступен — 503.
    """
    return await _publish_restart_signal(redis, _RESTART_OBSERVER_CHANNEL)

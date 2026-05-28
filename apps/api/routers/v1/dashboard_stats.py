# -*- coding: utf-8 -*-
"""Роутер dashboard-stats и dashboard-batch.

Endpoints (с prefix /api от auto-discovery):
    GET /dashboard/stats  — scalar-агрегации для overview-карточек.
    GET /dashboard/batch  — композит stats + 4 списка для одного fetch.

Партиционные WHERE по cycle_ts/created_at/started_at — обязательны.
asyncio.gather для параллельных subqueries.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.api.deps import DepEngine, DepRedis
from apps.api.routers.v1.schemas.dashboard_aggregates import (
    DashboardBatchOut,
    DashboardStatsOut,
)
from apps.api.utils.status_mapper import to_frontend_task_status
from core.dashboard.snapshot import build_ad_snapshot, build_incidents_snapshot

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

# Жёсткие лимиты для /batch (защита от bloating ответа).
_MAX_BATCH_LIMIT = 100
_OBSERVER_RUNTIME_KEY = "observer:runtime"


async def _read_observer_status(redis: Any) -> str:
    """Чтение observer:runtime из Redis для поля observer_status.

    При любой ошибке (нет ключа, Redis down, битый JSON) — возвращает 'unknown'.
    Никогда не падает с 5xx.
    """
    try:
        raw = await redis.get(_OBSERVER_RUNTIME_KEY)
    except Exception as exc:
        logger.warning("Не удалось прочитать observer:runtime: %s", exc)
        return "unknown"

    if raw is None:
        return "unknown"

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return "unknown"

    status = payload.get("status")
    if status in {"running", "paused"}:
        return status
    return "unknown"


async def _query_ad_counts(engine: AsyncEngine) -> dict[str, int]:
    """COUNT FbAd is_active + counts по alert_state из ad_alert_state.

    Один запрос — оптимально по числу round-trip'ов.
    """
    sql = """
        SELECT
            (SELECT COUNT(*) FROM fb_ads WHERE is_active = true) AS total_active,
            COUNT(*) FILTER (WHERE s.alert_state = 'normal' OR s.alert_state IS NULL)
                AS in_normal,
            COUNT(*) FILTER (WHERE s.alert_state = 'warning_sent') AS in_warning,
            COUNT(*) FILTER (WHERE s.alert_state = 'stop_sent') AS in_stop,
            COUNT(*) FILTER (WHERE s.alert_state = 'claimed') AS in_claimed,
            COUNT(*) FILTER (WHERE s.alert_state = 'disabled') AS in_disabled,
            COUNT(*) FILTER (
                WHERE s.alert_state IN ('warning_sent', 'stop_sent')
                AND (s.snoozed_until IS NULL OR s.snoozed_until < NOW())
            ) AS active_incidents
        FROM fb_ads
        LEFT JOIN ad_alert_state s ON s.ad_id = fb_ads.id
        WHERE fb_ads.is_active = true
    """
    async with engine.connect() as conn:
        result = await conn.execute(text(sql))
        row = result.one()

    return {
        "total_ads_monitored": int(row.total_active or 0),
        "ads_in_normal": int(row.in_normal or 0),
        "ads_in_warning": int(row.in_warning or 0),
        "ads_in_stop": int(row.in_stop or 0),
        "ads_in_claimed": int(row.in_claimed or 0),
        "ads_in_disabled": int(row.in_disabled or 0),
        "active_incidents": int(row.active_incidents or 0),
    }


async def _query_scan_counts(engine: AsyncEngine) -> dict[str, Any]:
    """Последний scan_run + counts за сутки. Partition pruning по started_at."""
    # Окно последних 7 дней для last_scan_at (если за неделю не было сканов — None).
    week_ago = datetime.now(UTC) - timedelta(days=7)
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

    sql_last_scan = """
        SELECT started_at, outcome
        FROM scan_runs
        WHERE started_at >= :week_ago
        ORDER BY started_at DESC
        LIMIT 1
    """
    sql_today_counts = """
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE outcome = 'error') AS with_errors
        FROM scan_runs
        WHERE started_at >= :today_start
    """

    async with engine.connect() as conn:
        last_row = (await conn.execute(text(sql_last_scan), {"week_ago": week_ago})).first()
        today_row = (await conn.execute(text(sql_today_counts), {"today_start": today_start})).one()

    return {
        "last_scan_at": last_row.started_at if last_row else None,
        "last_scan_outcome": last_row.outcome if last_row else None,
        "scans_today": int(today_row.total or 0),
        "scans_today_with_errors": int(today_row.with_errors or 0),
    }


async def _query_task_counts(engine: AsyncEngine) -> dict[str, int]:
    """Counts по task_queue: pending disable/enable + failed за 24h."""
    day_ago = datetime.now(UTC) - timedelta(hours=24)
    sql = """
        SELECT
            COUNT(*) FILTER (
                WHERE task_type = 'disable'
                AND status IN ('draft', 'pending', 'retrying')
            ) AS pending_disable,
            COUNT(*) FILTER (
                WHERE task_type = 'enable'
                AND status IN ('draft', 'pending', 'retrying')
            ) AS pending_enable,
            COUNT(*) FILTER (
                WHERE task_type IN ('disable', 'enable')
                AND status = 'failed'
                AND updated_at >= :day_ago
            ) AS failed_24h
        FROM task_queue
    """
    async with engine.connect() as conn:
        row = (await conn.execute(text(sql), {"day_ago": day_ago})).one()
    return {
        "pending_disable_tasks": int(row.pending_disable or 0),
        "pending_enable_tasks": int(row.pending_enable or 0),
        "failed_tasks_24h": int(row.failed_24h or 0),
    }


async def _build_stats(engine: AsyncEngine, redis: Any) -> DashboardStatsOut:
    """Собирает все 4 группы счётчиков параллельно через asyncio.gather.

    При ошибке в любом из подзапросов — пробрасываем дальше (fail-all для stats),
    чтобы фронт чётко видел проблему в overview-карточках.
    """
    ad_counts, scan_counts, task_counts, observer_status = await asyncio.gather(
        _query_ad_counts(engine),
        _query_scan_counts(engine),
        _query_task_counts(engine),
        _read_observer_status(redis),
    )

    return DashboardStatsOut(
        **ad_counts,
        **scan_counts,
        **task_counts,
        observer_status=observer_status,
    )


# ─────────────────────── GET /dashboard/stats ────────────────────────────────


@router.get("/dashboard/stats", response_model=DashboardStatsOut)
async def get_dashboard_stats(
    engine: DepEngine,
    redis: DepRedis,
) -> DashboardStatsOut:
    """Overview-карточки для DashboardPage. Никаких параметров.

    Параллельно (asyncio.gather) выполняет:
    - COUNT'ы по fb_ads + ad_alert_state (один SQL).
    - COUNT'ы по scan_runs (partitioned WHERE started_at).
    - COUNT'ы по task_queue (status='failed' за 24h + pending).
    - Чтение observer:runtime из Redis.

    Если Redis недоступен — observer_status='unknown', endpoint не падает.
    """
    return await _build_stats(engine, redis)


# ─────────────────────── GET /dashboard/batch ────────────────────────────────


async def _safe_call(coro, default):
    """Запускает coroutine, при exception — пишет warning и возвращает default.

    Partial-failure policy /batch: одна секция упала — остальные возвращаются,
    фронт видит пустой массив там, где была ошибка.
    """
    try:
        return await coro
    except Exception as exc:
        logger.warning("Подзапрос /dashboard/batch упал: %s", exc, exc_info=False)
        return default


async def _query_recent_disable_tasks(engine: AsyncEngine, limit: int) -> list[dict[str, Any]]:
    """Последние disable-задачи (top N) с JOIN'ом по fb_ads для ad_name."""
    sql = """
        SELECT
            tq.id,
            tq.task_type,
            tq.status,
            tq.payload->>'fb_ad_id' AS fb_ad_id,
            fa.ad_name,
            tq.attempt_count,
            tq.max_attempts,
            tq.requested_by,
            tq.created_by_chat_id,
            tq.created_at,
            tq.updated_at,
            tq.next_retry_at,
            tq.last_error
        FROM task_queue tq
        LEFT JOIN fb_ads fa ON fa.fb_ad_id = tq.payload->>'fb_ad_id'
        WHERE tq.task_type = 'disable'
        ORDER BY tq.created_at DESC
        LIMIT :lim
    """
    async with engine.connect() as conn:
        rows = (await conn.execute(text(sql), {"lim": limit})).fetchall()
    return [
        {
            "id": str(r.id),
            "fb_ad_id": r.fb_ad_id,
            "ad_name": r.ad_name,
            "task_type": r.task_type,
            "status": to_frontend_task_status(r.status),
            "attempt_count": r.attempt_count,
            "max_attempts": r.max_attempts,
            "requested_by": r.requested_by,
            "requested_by_chat_id": r.created_by_chat_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "next_attempt_at": r.next_retry_at.isoformat() if r.next_retry_at else None,
            "last_error_message": r.last_error,
        }
        for r in rows
    ]


async def _query_recent_alerts(engine: AsyncEngine, limit: int) -> list[dict[str, Any]]:
    """Последние alert_events за окно 24h. Partition pruning по created_at."""
    from_dt = datetime.now(UTC) - timedelta(hours=24)
    sql = """
        SELECT
            ae.id              AS id,
            ae.stage           AS stage,
            ae.matched_rule_codes AS matched_rule_codes,
            ae.metrics_json    AS metrics_json,
            ae.created_at      AS created_at,
            fb_ads.fb_ad_id    AS fb_ad_id,
            fb_ads.ad_name     AS ad_name,
            fb_campaigns.campaign_name AS campaign_name,
            offers.code        AS offer_code
        FROM alert_events ae
        LEFT JOIN fb_ads      ON fb_ads.id = ae.ad_id
        LEFT JOIN fb_adsets   ON fb_adsets.id = fb_ads.adset_id
        LEFT JOIN fb_campaigns ON fb_campaigns.id = fb_adsets.campaign_id
        LEFT JOIN offers      ON offers.id = fb_campaigns.offer_id
        WHERE ae.created_at >= :from_dt
        ORDER BY ae.created_at DESC
        LIMIT :lim
    """
    async with engine.connect() as conn:
        rows = (await conn.execute(text(sql), {"from_dt": from_dt, "lim": limit})).fetchall()
    return [
        {
            "id": str(r.id),
            "fb_ad_id": r.fb_ad_id,
            "ad_name": r.ad_name,
            "campaign_name": r.campaign_name,
            "offer_code": r.offer_code,
            "stage": r.stage,
            "matched_rule_codes": list(r.matched_rule_codes or []),
            "triggered_by_rule_codes": None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "alert_payload": r.metrics_json if r.metrics_json else None,
        }
        for r in rows
    ]


async def _query_enable_recommendations_pending(
    engine: AsyncEngine, limit: int
) -> list[dict[str, Any]]:
    """Топ N рекомендаций без promoted_to_task_id (ожидают подтверждения)."""
    sql = """
        SELECT
            er.id,
            fa.fb_ad_id,
            fa.ad_name,
            fc.campaign_name,
            er.recommendation_level,
            er.snapshot_metrics,
            er.created_at,
            er.live_batch_started_at,
            er.promoted_to_task_id
        FROM enable_recommendations er
        JOIN fb_ads fa ON fa.id = er.ad_id
        JOIN fb_adsets fas ON fas.id = fa.adset_id
        JOIN fb_campaigns fc ON fc.id = fas.campaign_id
        WHERE er.promoted_to_task_id IS NULL
        ORDER BY er.created_at DESC
        LIMIT :lim
    """
    async with engine.connect() as conn:
        rows = (await conn.execute(text(sql), {"lim": limit})).fetchall()
    return [
        {
            "id": str(r.id),
            "fb_ad_id": r.fb_ad_id,
            "ad_name": r.ad_name,
            "campaign_name": r.campaign_name,
            "reason": r.recommendation_level,
            "recommendation_level": r.recommendation_level,
            "metrics_payload": r.snapshot_metrics,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "live_batch_started_at": (
                r.live_batch_started_at.isoformat() if r.live_batch_started_at else None
            ),
            "promoted_to_task_id": r.promoted_to_task_id,
            "promoted_task_status": None,
        }
        for r in rows
    ]


@router.get("/dashboard/batch", response_model=DashboardBatchOut)
async def get_dashboard_batch(
    engine: DepEngine,
    redis: DepRedis,
    incidents_limit: int = Query(default=10, ge=1, le=_MAX_BATCH_LIMIT),
    alerts_limit: int = Query(default=20, ge=1, le=_MAX_BATCH_LIMIT),
    disable_limit: int = Query(default=10, ge=1, le=_MAX_BATCH_LIMIT),
) -> DashboardBatchOut:
    """Композит stats + recent_incidents + recent_alerts + recent_disable_tasks
    + enable_recommendations_pending.

    Снижает количество fetch'ей на DashboardPage с 5 до 1.
    Поведение при partial failure: если один из подзапросов падает —
    возвращаем для него default (пустой массив или нулевой stats).
    Остальные секции возвращаются. Это согласовано с UX: фронт не отображает
    ошибку всему экрану, если упала одна секция.
    """
    # Параллельные подзапросы. Stats — fail-all через _build_stats внутри.
    # Списки — safe (fail-section, fallback пустой).
    stats, incidents, alerts, disable_tasks, recos = await asyncio.gather(
        _safe_call(_build_stats(engine, redis), DashboardStatsOut()),
        _safe_call(build_incidents_snapshot(engine, stage="all", limit=incidents_limit), []),
        _safe_call(_query_recent_alerts(engine, alerts_limit), []),
        _safe_call(_query_recent_disable_tasks(engine, disable_limit), []),
        _safe_call(_query_enable_recommendations_pending(engine, 5), []),
    )

    return DashboardBatchOut(
        stats=stats,
        recent_incidents=incidents,
        recent_alerts=alerts,
        recent_disable_tasks=disable_tasks,
        enable_recommendations_pending=recos,
    )


# Импорт сохраняем чтобы build_ad_snapshot можно было вызывать (для будущей расширяемости)
__all__ = ["router", "build_ad_snapshot"]

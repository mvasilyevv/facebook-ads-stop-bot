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
from apps.api.utils.alert_serializer import alert_event_row_to_out
from apps.api.utils.task_serializer import task_row_to_out
from core.dashboard.snapshot import build_ad_snapshot, build_incidents_snapshot
from core.observer.runtime import read_observer_runtime
from core.tasks.channel import disable_channel_sql, enable_channel_sql, target_id_sql

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

# Жёсткие лимиты для /batch (защита от bloating ответа).
_MAX_BATCH_LIMIT = 100


async def _read_observer_status(redis: Any) -> str:
    """Чтение нормализованного статуса observer из Redis.

    Делегирует в read_observer_runtime() — единственную точку чтения observer:runtime.
    При любой ошибке возвращает 'unknown'. Никогда не падает с 5xx.
    """
    result = await read_observer_runtime(redis)
    return result["status"]


async def _query_ad_counts(engine: AsyncEngine) -> dict[str, int]:
    """Counts по объявлениям в ТЕКУЩЕМ скопе наблюдения + разбивка по alert_state.

    «Под наблюдением» = is_active И виден в последнем УСПЕШНОМ скане (last_seen_at
    не старше его started_at). Так счётчик отражает реально сканируемые объявления
    (с учётом allowlist кампаний / owner-тега), а не весь накопленный каталог: при
    сужении скопа «замороженные» объявления старых сканов сразу выпадают, а не висят
    в плашке сутками. CTE scope: граница — последний завершённый **success**-скан
    (который РЕАЛЬНО видел объявления). `empty`-сканы НЕ двигают границу: пустой скан —
    это транзиентная слепота сканера (страница не догрузилась / am_tabular пусто), он
    не обновляет last_seen, и если им двигать scope — ВСЕ объявления выпадают из окна и
    дашборд схлопывается в 0 (мнимое «всё исчезло»). Фолбэк NOW()-24h если success-сканов
    ещё не было. Один запрос — оптимально по числу round-trip'ов.
    """
    sql = """
        WITH scope AS (
            SELECT COALESCE(
                (SELECT MAX(started_at) FROM scan_runs
                   WHERE outcome = 'success' AND finished_at IS NOT NULL),
                NOW() - INTERVAL '24 hours'
            ) AS since
        )
        SELECT
            COUNT(*) AS total_active,
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
        CROSS JOIN scope
        LEFT JOIN ad_alert_state s ON s.ad_id = fb_ads.id
        WHERE fb_ads.is_active = true
          AND fb_ads.last_seen_at >= scope.since
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
    # Отключение/включение после удаления DOM — meta_api_mutation pause_ad/activate_ad
    # (+ legacy disable/enable). Иначе счётчики всегда 0.
    disable_pred = disable_channel_sql("task_queue")
    enable_pred = enable_channel_sql("task_queue")
    sql = f"""
        SELECT
            COUNT(*) FILTER (
                WHERE {disable_pred}
                AND status IN ('draft', 'pending', 'retrying')
            ) AS pending_disable,
            COUNT(*) FILTER (
                WHERE {enable_pred}
                AND status IN ('draft', 'pending', 'retrying')
            ) AS pending_enable,
            COUNT(*) FILTER (
                WHERE ({disable_pred} OR {enable_pred})
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
    target_expr = target_id_sql("tq")
    sql = f"""
        SELECT
            tq.id,
            tq.task_type,
            tq.status,
            {target_expr} AS fb_ad_id,
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
        LEFT JOIN fb_ads fa ON fa.fb_ad_id = {target_expr}
        WHERE {disable_channel_sql("tq")}
        ORDER BY tq.created_at DESC
        LIMIT :lim
    """
    async with engine.connect() as conn:
        rows = (await conn.execute(text(sql), {"lim": limit})).fetchall()
    return [task_row_to_out(r) for r in rows]


async def _query_recent_enable_tasks(engine: AsyncEngine, limit: int) -> list[dict[str, Any]]:
    """Последние enable-задачи (top N) с JOIN'ом по fb_ads для ad_name.

    Аналог _query_recent_disable_tasks для канала включения объявлений
    (meta_api_mutation activate_ad + legacy enable task_type).
    """
    target_expr = target_id_sql("tq")
    sql = f"""
        SELECT
            tq.id,
            tq.task_type,
            tq.status,
            {target_expr} AS fb_ad_id,
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
        LEFT JOIN fb_ads fa ON fa.fb_ad_id = {target_expr}
        WHERE {enable_channel_sql("tq")}
        ORDER BY tq.created_at DESC
        LIMIT :lim
    """
    async with engine.connect() as conn:
        rows = (await conn.execute(text(sql), {"lim": limit})).fetchall()
    return [task_row_to_out(r) for r in rows]


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
            fb_campaigns.ad_account_id AS ad_account_id,
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
    return [alert_event_row_to_out(r) for r in rows]


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
    enable_limit: int = Query(default=10, ge=1, le=_MAX_BATCH_LIMIT),
) -> DashboardBatchOut:
    """Композит stats + recent_incidents + recent_alerts + recent_disable_tasks
    + recent_enable_tasks + enable_recommendations_pending.

    Снижает количество fetch'ей на DashboardPage с 6 до 1.
    Поведение при partial failure: если один из подзапросов падает —
    возвращаем для него default (пустой массив или нулевой stats).
    Остальные секции возвращаются. Это согласовано с UX: фронт не отображает
    ошибку всему экрану, если упала одна секция.

    recent_enable_tasks — задачи включения объявлений (activate_ad), аналог
    recent_disable_tasks. Управляется отдельным параметром enable_limit.
    """
    # Параллельные подзапросы. Stats — fail-all через _build_stats внутри.
    # Списки — safe (fail-section, fallback пустой).
    stats, incidents, alerts, disable_tasks, enable_tasks, recos = await asyncio.gather(
        _safe_call(_build_stats(engine, redis), DashboardStatsOut()),
        _safe_call(build_incidents_snapshot(engine, stage="all", limit=incidents_limit), []),
        _safe_call(_query_recent_alerts(engine, alerts_limit), []),
        _safe_call(_query_recent_disable_tasks(engine, disable_limit), []),
        _safe_call(_query_recent_enable_tasks(engine, enable_limit), []),
        _safe_call(_query_enable_recommendations_pending(engine, 5), []),
    )

    return DashboardBatchOut(
        stats=stats,
        recent_incidents=incidents,
        recent_alerts=alerts,
        recent_disable_tasks=disable_tasks,
        recent_enable_tasks=enable_tasks,
        enable_recommendations_pending=recos,
    )


# Импорт сохраняем чтобы build_ad_snapshot можно было вызывать (для будущей расширяемости)
__all__ = ["router", "build_ad_snapshot"]

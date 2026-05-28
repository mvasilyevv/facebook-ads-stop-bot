# -*- coding: utf-8 -*-
"""Роутер HistoryPage — 6 endpoints агрегации за произвольный период.

Endpoints (prefix /api от auto-discovery):
    GET /history/summary    — сводка: spend/impressions/clicks/leads/deps + алерты + задачи
    GET /history/timeline   — UNION ALL алертов и задач, сорт по timestamp DESC
    GET /history/campaigns  — GROUP BY кампании, сорт по spend DESC
    GET /history/events     — только AlertEvent с drill-down по campaign_id/stage
    GET /history/offers     — GROUP BY офферу через JOIN fb_campaigns.offer_id
    GET /history/ads        — GROUP BY объявлению + last_alert + last_disable

Все endpoint'ы:
- Принимают from_iso/to_iso (ISO-8601), default — последние 30 дней.
- Диапазон не может превышать 90 дней (иначе 422).
- Все partitioned-таблицы (ad_metrics, alert_events) фильтруются по partition-key:
    ad_metrics   → WHERE cycle_ts BETWEEN :from AND :to
    alert_events → WHERE created_at BETWEEN :from AND :to
- matched_rule_codes — JSONB! Unnest через jsonb_array_elements_text().
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from apps.api.deps import DepEngine
from apps.api.routers.v1.schemas.history import (
    HistoryAdOut,
    HistoryAlerts,
    HistoryCampaignOut,
    HistoryEventOut,
    HistoryOfferOut,
    HistoryRuleCount,
    HistorySummaryOut,
    HistoryTasks,
    HistoryTimelineItem,
    HistoryTotals,
)
from apps.api.utils.partition import default_window
from apps.api.utils.status_mapper import to_frontend_task_status
from core.dashboard.metric_aggregation import latest_per_ad_per_day_cte

logger = logging.getLogger(__name__)

router = APIRouter(tags=["history"])

# Жёсткие лимиты
_MAX_RANGE_DAYS = 90
_DEFAULT_RANGE_HOURS = 720  # 30 дней
_TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")


def _parse_window(
    from_iso: str | None,
    to_iso: str | None,
) -> tuple[datetime, datetime]:
    """Парсит временное окно из параметров запроса.

    Defaults: from = now - 30 дней, to = now.
    Валидация: to >= from, range <= 90 дней.
    """
    if from_iso is None and to_iso is None:
        return default_window(hours=_DEFAULT_RANGE_HOURS)

    try:
        from_dt = (
            datetime.fromisoformat(from_iso)
            if from_iso
            else default_window(hours=_DEFAULT_RANGE_HOURS)[0]
        )
        to_dt = datetime.fromisoformat(to_iso) if to_iso else datetime.now(UTC)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"Неверный формат даты: {exc}") from exc

    # Нормализуем aware-UTC
    if from_dt.tzinfo is None:
        from_dt = from_dt.replace(tzinfo=UTC)
    if to_dt.tzinfo is None:
        to_dt = to_dt.replace(tzinfo=UTC)

    if to_dt < from_dt:
        raise HTTPException(status_code=422, detail="to_iso должен быть >= from_iso")

    if (to_dt - from_dt) > timedelta(days=_MAX_RANGE_DAYS):
        raise HTTPException(
            status_code=422,
            detail=f"Диапазон не может превышать {_MAX_RANGE_DAYS} дней",
        )

    return from_dt, to_dt


def _fmt_decimal(value: Decimal | None) -> str:
    """Форматирует Decimal в строку с 2 знаками. None → '0.00'."""
    if value is None:
        return "0.00"
    return f"{value:.2f}"


def _safe_cpl(spend: Decimal | None, leads: int) -> str | None:
    """Вычисляет cost_per_lead = spend / leads. Возвращает None если leads == 0."""
    if not leads or spend is None:
        return None
    return _fmt_decimal(spend / leads)


# ─────────────────────── GET /history/summary ────────────────────────────────


@router.get("/history/summary", response_model=HistorySummaryOut)
async def get_history_summary(
    engine: DepEngine,
    from_iso: str | None = Query(default=None, description="ISO-8601 начало периода"),
    to_iso: str | None = Query(default=None, description="ISO-8601 конец периода"),
) -> HistorySummaryOut:
    """Сводная агрегация за период: spend/метрики + алерты + задачи.

    Партиционированные таблицы фильтруются по partition-key.
    """
    from_dt, to_dt = _parse_window(from_iso, to_iso)

    async with engine.connect() as conn:
        # 1. Суммы метрик — партиционированная ad_metrics (cycle_ts).
        # CRIT-1: кумулятивные snapshot'ы, spend сбрасывается посуточно. Окно
        # многодневное (до 90д) → latest-per-ad-per-day, затем SUM (дневные итоги).
        metrics_sql = text(
            f"""
            WITH {latest_per_ad_per_day_cte(cte_alias="per_ad_day")}
            SELECT
                COALESCE(SUM(spend), 0)         AS spend,
                COALESCE(SUM(impressions), 0)   AS impressions,
                COALESCE(SUM(clicks), 0)        AS clicks,
                COALESCE(SUM(leads), 0)         AS leads,
                COALESCE(SUM(registrations), 0) AS registrations,
                COALESCE(SUM(deposits), 0)      AS deposits
            FROM per_ad_day
            """
        )
        m_row = (await conn.execute(metrics_sql, {"from_dt": from_dt, "to_dt": to_dt})).one()

        # 2. Количество активных объявлений (last_seen >= now - 7d)
        active_sql = text(
            """
            SELECT COUNT(*) AS cnt
            FROM fb_ads
            WHERE last_seen_at >= NOW() - INTERVAL '7 days'
              AND is_active = true
            """
        )
        active_row = (await conn.execute(active_sql)).one()

        # 3. Алерты по stage — партиционированная alert_events (created_at)
        alerts_sql = text(
            """
            SELECT stage, COUNT(*) AS cnt
            FROM alert_events
            WHERE created_at BETWEEN :from_dt AND :to_dt
            GROUP BY stage
            """
        )
        alert_rows = (
            await conn.execute(alerts_sql, {"from_dt": from_dt, "to_dt": to_dt})
        ).fetchall()
        warning_count = 0
        stop_count = 0
        for r in alert_rows:
            if r.stage == "warning":
                warning_count = r.cnt
            elif r.stage == "stop":
                stop_count = r.cnt

        # 4. Срабатывания по правилам — unnest JSONB matched_rule_codes
        rules_sql = text(
            """
            SELECT rule_code, COUNT(*) AS cnt
            FROM alert_events,
                 jsonb_array_elements_text(matched_rule_codes) AS rule_code
            WHERE created_at BETWEEN :from_dt AND :to_dt
            GROUP BY rule_code
            ORDER BY cnt DESC
            """
        )
        rule_rows = (await conn.execute(rules_sql, {"from_dt": from_dt, "to_dt": to_dt})).fetchall()
        by_rule = [HistoryRuleCount(rule_code=r.rule_code, count=r.cnt) for r in rule_rows]

        # 5. Задачи (disable/enable) — task_queue по updated_at (не партиционирована)
        tasks_sql = text(
            """
            SELECT
                COUNT(*) FILTER (WHERE task_type = 'disable' AND status = 'succeeded') AS disable_completed,
                COUNT(*) FILTER (WHERE task_type = 'disable' AND status = 'failed')    AS disable_failed,
                COUNT(*) FILTER (WHERE task_type = 'enable'  AND status = 'succeeded') AS enable_completed
            FROM task_queue
            WHERE task_type IN ('disable', 'enable')
              AND updated_at BETWEEN :from_dt AND :to_dt
            """
        )
        t_row = (await conn.execute(tasks_sql, {"from_dt": from_dt, "to_dt": to_dt})).one()

    return HistorySummaryOut(
        from_iso=from_dt,
        to_iso=to_dt,
        totals=HistoryTotals(
            spend=_fmt_decimal(m_row.spend),
            impressions=int(m_row.impressions),
            clicks=int(m_row.clicks),
            leads=int(m_row.leads),
            registrations=int(m_row.registrations),
            deposits=int(m_row.deposits),
            active_ads_count=int(active_row.cnt),
        ),
        alerts=HistoryAlerts(
            warning_count=int(warning_count),
            stop_count=int(stop_count),
            by_rule=by_rule,
        ),
        tasks=HistoryTasks(
            disable_completed=int(t_row.disable_completed),
            disable_failed=int(t_row.disable_failed),
            enable_completed=int(t_row.enable_completed),
        ),
    )


# ─────────────────────── GET /history/timeline ───────────────────────────────


@router.get("/history/timeline", response_model=list[HistoryTimelineItem])
async def get_history_timeline(
    engine: DepEngine,
    from_iso: str | None = Query(default=None),
    to_iso: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[HistoryTimelineItem]:
    """Объединённая лента: AlertEvent + terminal TaskQueue (succeeded/failed/cancelled).

    UNION ALL с JOIN FbAd→FbAdset→FbCampaign для имён. Сорт по ts DESC.
    Только terminal задачи (succeeded/failed/cancelled) — pending/running не включаются.
    """
    from_dt, to_dt = _parse_window(from_iso, to_iso)

    sql = text(
        """
        SELECT
            'alert'         AS event_type,
            ae.created_at   AS ts,
            a.fb_ad_id,
            a.ad_name,
            c.campaign_name,
            ae.stage,
            ae.matched_rule_codes::text  AS rule_codes_raw,
            NULL::text      AS task_type,
            NULL::text      AS task_status
        FROM alert_events ae
        JOIN fb_ads a     ON a.id = ae.ad_id
        JOIN fb_adsets s  ON s.id = a.adset_id
        JOIN fb_campaigns c ON c.id = s.campaign_id
        WHERE ae.created_at BETWEEN :from_dt AND :to_dt

        UNION ALL

        SELECT
            'task'              AS event_type,
            tq.updated_at       AS ts,
            tq.payload->>'fb_ad_id'  AS fb_ad_id,
            a.ad_name,
            c.campaign_name,
            NULL                AS stage,
            NULL                AS rule_codes_raw,
            tq.task_type,
            tq.status           AS task_status
        FROM task_queue tq
        LEFT JOIN fb_ads a    ON a.fb_ad_id = tq.payload->>'fb_ad_id'
        LEFT JOIN fb_adsets s ON s.id = a.adset_id
        LEFT JOIN fb_campaigns c ON c.id = s.campaign_id
        WHERE tq.task_type IN ('disable', 'enable')
          AND tq.status IN ('succeeded', 'failed', 'cancelled')
          AND tq.updated_at BETWEEN :from_dt AND :to_dt

        ORDER BY ts DESC
        LIMIT :limit
        """
    )

    import json as _json

    async with engine.connect() as conn:
        rows = (
            await conn.execute(sql, {"from_dt": from_dt, "to_dt": to_dt, "limit": limit})
        ).fetchall()

    result: list[HistoryTimelineItem] = []
    for r in rows:
        rule_codes: list[str] | None = None
        if r.rule_codes_raw:
            try:
                rule_codes = _json.loads(r.rule_codes_raw)
            except Exception:
                rule_codes = []

        task_status_fe: str | None = None
        if r.task_status:
            try:
                task_status_fe = to_frontend_task_status(r.task_status)
            except ValueError:
                task_status_fe = r.task_status.upper()

        result.append(
            HistoryTimelineItem(
                event_type=r.event_type,
                ts=r.ts,
                fb_ad_id=r.fb_ad_id,
                ad_name=r.ad_name,
                campaign_name=r.campaign_name,
                stage=r.stage,
                rule_codes=rule_codes,
                task_type=r.task_type,
                task_status=task_status_fe,
            )
        )
    return result


# ─────────────────────── GET /history/campaigns ──────────────────────────────


@router.get("/history/campaigns", response_model=list[HistoryCampaignOut])
async def get_history_campaigns(
    engine: DepEngine,
    from_iso: str | None = Query(default=None),
    to_iso: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[HistoryCampaignOut]:
    """GROUP BY кампании: spend/leads/deps + active_ads_count + alerts_count.

    Партиционированная ad_metrics фильтруется по cycle_ts.
    active_ads_count — через last_seen_at >= NOW() - 7d.
    """
    from_dt, to_dt = _parse_window(from_iso, to_iso)

    # CRIT-1: ad_metrics кумулятивна, spend сбрасывается посуточно. Окно до 90д →
    # per-ad-per-day latest, затем SUM до per-ad-итога в CTE per_ad. Внешний SUM
    # по кампании складывает per-ad-итоги (каждый ad джойнится один раз).
    sql = text(
        f"""
        WITH {
            latest_per_ad_per_day_cte(
                cte_alias="per_ad_day",
                columns=("spend", "leads", "registrations", "deposits"),
            )
        },
        per_ad AS (
            SELECT
                ad_id,
                SUM(spend)         AS spend,
                SUM(leads)         AS leads,
                SUM(registrations) AS registrations,
                SUM(deposits)      AS deposits
            FROM per_ad_day
            GROUP BY ad_id
        )
        SELECT
            c.id                        AS campaign_id,
            c.fb_campaign_id,
            c.campaign_name,
            o.code                      AS offer_code,
            COALESCE(SUM(m.spend), 0)           AS spend,
            COALESCE(SUM(m.leads), 0)::int      AS leads,
            COALESCE(SUM(m.registrations), 0)::int AS registrations,
            COALESCE(SUM(m.deposits), 0)::int   AS deposits,
            COUNT(DISTINCT a.id) FILTER (
                WHERE a.last_seen_at >= NOW() - INTERVAL '7 days'
            )::int                              AS active_ads_count,
            COALESCE(al.alerts_count, 0)::int   AS alerts_count
        FROM fb_campaigns c
        JOIN fb_adsets s    ON s.campaign_id = c.id
        JOIN fb_ads a       ON a.adset_id = s.id
        LEFT JOIN offers o  ON o.id = c.offer_id
        LEFT JOIN per_ad m  ON m.ad_id = a.id
        LEFT JOIN (
            SELECT ae.ad_id, COUNT(*) AS alerts_count
            FROM alert_events ae
            WHERE ae.created_at BETWEEN :from_dt AND :to_dt
            GROUP BY ae.ad_id
        ) al ON al.ad_id = a.id
        GROUP BY c.id, c.fb_campaign_id, c.campaign_name, o.code, al.alerts_count
        ORDER BY spend DESC
        LIMIT :limit
        """
    )

    async with engine.connect() as conn:
        rows = (
            await conn.execute(sql, {"from_dt": from_dt, "to_dt": to_dt, "limit": limit})
        ).fetchall()

    result = []
    for r in rows:
        spend = r.spend
        leads = r.leads
        result.append(
            HistoryCampaignOut(
                campaign_id=str(r.campaign_id),
                fb_campaign_id=r.fb_campaign_id,
                campaign_name=r.campaign_name,
                offer_code=r.offer_code,
                spend=_fmt_decimal(spend),
                leads=leads,
                registrations=r.registrations,
                deposits=r.deposits,
                active_ads_count=r.active_ads_count,
                alerts_count=r.alerts_count,
                cost_per_lead=_safe_cpl(spend, leads),
            )
        )
    return result


# ─────────────────────── GET /history/events ─────────────────────────────────


@router.get("/history/events", response_model=list[HistoryEventOut])
async def get_history_events(
    engine: DepEngine,
    from_iso: str | None = Query(default=None),
    to_iso: str | None = Query(default=None),
    campaign_id: str | None = Query(default=None, description="UUID кампании для drill-down"),
    fb_ad_id: str | None = Query(default=None, description="fb_ad_id конкретного объявления"),
    stage: str | None = Query(default=None, description="warning | stop"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[HistoryEventOut]:
    """AlertEvent drill-down с JOIN ad/adset/campaign/offer.

    Партиционированная alert_events фильтруется по created_at.
    """
    from_dt, to_dt = _parse_window(from_iso, to_iso)

    # Валидация stage
    if stage and stage not in ("warning", "stop"):
        raise HTTPException(status_code=422, detail="stage должен быть 'warning' или 'stop'")

    # Конвертируем campaign_id в UUID-объект если передан (asyncpg не понимает ::uuid cast в text())
    campaign_uuid: uuid.UUID | None = None
    if campaign_id:
        try:
            campaign_uuid = uuid.UUID(campaign_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Неверный campaign_id: {exc}") from exc

    # Базовый SQL с опциональными фильтрами через WHERE-блоки
    campaign_filter = "AND c.id = :campaign_uuid" if campaign_uuid else ""
    ad_filter = "AND a.fb_ad_id = :fb_ad_id" if fb_ad_id else ""
    stage_filter = "AND ae.stage = :stage" if stage else ""

    sql = text(
        f"""
        SELECT
            ae.id           AS id,
            a.fb_ad_id,
            a.ad_name,
            c.campaign_name,
            o.code          AS offer_code,
            ae.stage,
            ae.matched_rule_codes,
            ae.created_at,
            ae.metrics_json AS alert_payload
        FROM alert_events ae
        JOIN fb_ads a       ON a.id = ae.ad_id
        JOIN fb_adsets s    ON s.id = a.adset_id
        JOIN fb_campaigns c ON c.id = s.campaign_id
        LEFT JOIN offers o  ON o.id = c.offer_id
        WHERE ae.created_at BETWEEN :from_dt AND :to_dt
          {campaign_filter}
          {ad_filter}
          {stage_filter}
        ORDER BY ae.created_at DESC
        LIMIT :limit
        """
    )

    params: dict = {"from_dt": from_dt, "to_dt": to_dt, "limit": limit}
    if campaign_uuid:
        params["campaign_uuid"] = campaign_uuid
    if fb_ad_id:
        params["fb_ad_id"] = fb_ad_id
    if stage:
        params["stage"] = stage

    async with engine.connect() as conn:
        rows = (await conn.execute(sql, params)).fetchall()

    return [
        HistoryEventOut(
            id=str(r.id),
            fb_ad_id=r.fb_ad_id,
            ad_name=r.ad_name,
            campaign_name=r.campaign_name,
            offer_code=r.offer_code,
            stage=r.stage,
            matched_rule_codes=r.matched_rule_codes or [],
            created_at=r.created_at,
            alert_payload=r.alert_payload,
        )
        for r in rows
    ]


# ─────────────────────── GET /history/offers ─────────────────────────────────


@router.get("/history/offers", response_model=list[HistoryOfferOut])
async def get_history_offers(
    engine: DepEngine,
    from_iso: str | None = Query(default=None),
    to_iso: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[HistoryOfferOut]:
    """GROUP BY Offer через JOIN fb_campaigns.offer_id.

    Партиционированные таблицы фильтруются по partition-key.
    """
    from_dt, to_dt = _parse_window(from_iso, to_iso)

    # CRIT-1: per-ad-per-day latest → per-ad SUM в CTE, чтобы spend не завышался
    # кумулятивом и посуточным reset'ом. alert_events джойнится отдельно (count).
    sql = text(
        f"""
        WITH {
            latest_per_ad_per_day_cte(
                cte_alias="per_ad_day",
                columns=("spend", "leads", "registrations", "deposits"),
            )
        },
        per_ad AS (
            SELECT
                ad_id,
                SUM(spend)         AS spend,
                SUM(leads)         AS leads,
                SUM(registrations) AS registrations,
                SUM(deposits)      AS deposits
            FROM per_ad_day
            GROUP BY ad_id
        )
        SELECT
            o.id            AS offer_id,
            o.code          AS offer_code,
            o.name          AS offer_name,
            COALESCE(SUM(m.spend), 0)           AS spend,
            COALESCE(SUM(m.leads), 0)::int      AS leads,
            COALESCE(SUM(m.registrations), 0)::int AS registrations,
            COALESCE(SUM(m.deposits), 0)::int   AS deposits,
            COALESCE(COUNT(DISTINCT ae.id), 0)::int AS alerts_count,
            COUNT(DISTINCT a.id) FILTER (
                WHERE a.last_seen_at >= NOW() - INTERVAL '7 days'
            )::int                              AS active_ads_count
        FROM offers o
        JOIN fb_campaigns c ON c.offer_id = o.id
        JOIN fb_adsets s    ON s.campaign_id = c.id
        JOIN fb_ads a       ON a.adset_id = s.id
        LEFT JOIN per_ad m  ON m.ad_id = a.id
        LEFT JOIN alert_events ae
            ON ae.ad_id = a.id
            AND ae.created_at BETWEEN :from_dt AND :to_dt
        GROUP BY o.id, o.code, o.name
        ORDER BY spend DESC
        LIMIT :limit
        """
    )

    async with engine.connect() as conn:
        rows = (
            await conn.execute(sql, {"from_dt": from_dt, "to_dt": to_dt, "limit": limit})
        ).fetchall()

    result = []
    for r in rows:
        spend = r.spend
        leads = r.leads
        result.append(
            HistoryOfferOut(
                offer_id=str(r.offer_id),
                offer_code=r.offer_code,
                offer_name=r.offer_name,
                spend=_fmt_decimal(spend),
                leads=leads,
                registrations=r.registrations,
                deposits=r.deposits,
                alerts_count=r.alerts_count,
                active_ads_count=r.active_ads_count,
                cost_per_lead=_safe_cpl(spend, leads),
            )
        )
    return result


# ─────────────────────── GET /history/ads ────────────────────────────────────


@router.get("/history/ads", response_model=list[HistoryAdOut])
async def get_history_ads(
    engine: DepEngine,
    from_iso: str | None = Query(default=None),
    to_iso: str | None = Query(default=None),
    campaign_id: str | None = Query(default=None, description="UUID кампании (фильтр)"),
    offer_id: str | None = Query(default=None, description="UUID оффера (фильтр)"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[HistoryAdOut]:
    """GROUP BY объявлению: spend/leads/deps + last_alert + last_disable.

    last_alert_at / last_alert_stage — через LATERAL subquery.
    last_disable_at — через LATERAL subquery по task_queue.
    Партиционированные таблицы фильтруются по partition-key.
    """
    from_dt, to_dt = _parse_window(from_iso, to_iso)

    # Конвертируем campaign_id / offer_id в UUID-объекты (asyncpg не понимает ::uuid cast в text())
    campaign_uuid: uuid.UUID | None = None
    offer_uuid: uuid.UUID | None = None
    if campaign_id:
        try:
            campaign_uuid = uuid.UUID(campaign_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Неверный campaign_id: {exc}") from exc
    if offer_id:
        try:
            offer_uuid = uuid.UUID(offer_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Неверный offer_id: {exc}") from exc

    campaign_filter = "AND c.id = :campaign_uuid" if campaign_uuid else ""
    offer_filter = "AND o.id = :offer_uuid" if offer_uuid else ""

    # CRIT-1: per-ad-per-day latest → per-ad SUM, иначе кумулятив + посуточный
    # reset завышают spend. Внешний SUM по a.id берёт единственную per-ad строку.
    sql = text(
        f"""
        WITH {
            latest_per_ad_per_day_cte(
                cte_alias="per_ad_day",
                columns=("spend", "leads", "deposits"),
            )
        },
        per_ad AS (
            SELECT
                ad_id,
                SUM(spend)    AS spend,
                SUM(leads)    AS leads,
                SUM(deposits) AS deposits
            FROM per_ad_day
            GROUP BY ad_id
        )
        SELECT
            a.fb_ad_id,
            a.id            AS internal_id,
            a.ad_name,
            c.campaign_name,
            o.code          AS offer_code,
            a.is_active,
            COALESCE(SUM(m.spend), 0)           AS spend,
            COALESCE(SUM(m.leads), 0)::int      AS leads,
            COALESCE(SUM(m.deposits), 0)::int   AS deposits,
            la.last_alert_at,
            la.last_alert_stage,
            ld.last_disable_at,
            COALESCE(alc.alerts_count, 0)::int  AS alerts_count_in_window
        FROM fb_ads a
        JOIN fb_adsets s    ON s.id = a.adset_id
        JOIN fb_campaigns c ON c.id = s.campaign_id
        LEFT JOIN offers o  ON o.id = c.offer_id
        LEFT JOIN per_ad m  ON m.ad_id = a.id
        -- LATERAL: последний алерт в окне
        LEFT JOIN LATERAL (
            SELECT ae.created_at AS last_alert_at, ae.stage AS last_alert_stage
            FROM alert_events ae
            WHERE ae.ad_id = a.id
              AND ae.created_at BETWEEN :from_dt AND :to_dt
            ORDER BY ae.created_at DESC
            LIMIT 1
        ) la ON true
        -- LATERAL: последний disable в окне
        LEFT JOIN LATERAL (
            SELECT tq.updated_at AS last_disable_at
            FROM task_queue tq
            WHERE tq.task_type = 'disable'
              AND tq.status = 'succeeded'
              AND tq.payload->>'fb_ad_id' = a.fb_ad_id
              AND tq.updated_at BETWEEN :from_dt AND :to_dt
            ORDER BY tq.updated_at DESC
            LIMIT 1
        ) ld ON true
        -- Количество алертов в окне
        LEFT JOIN (
            SELECT ae.ad_id, COUNT(*) AS alerts_count
            FROM alert_events ae
            WHERE ae.created_at BETWEEN :from_dt AND :to_dt
            GROUP BY ae.ad_id
        ) alc ON alc.ad_id = a.id
        WHERE 1=1
          {campaign_filter}
          {offer_filter}
        GROUP BY
            a.id, a.fb_ad_id, a.ad_name, a.is_active,
            c.campaign_name, o.code,
            la.last_alert_at, la.last_alert_stage,
            ld.last_disable_at,
            alc.alerts_count
        ORDER BY spend DESC
        LIMIT :limit
        """
    )

    params: dict = {"from_dt": from_dt, "to_dt": to_dt, "limit": limit}
    if campaign_uuid:
        params["campaign_uuid"] = campaign_uuid
    if offer_uuid:
        params["offer_uuid"] = offer_uuid

    async with engine.connect() as conn:
        rows = (await conn.execute(sql, params)).fetchall()

    return [
        HistoryAdOut(
            fb_ad_id=r.fb_ad_id,
            internal_id=str(r.internal_id),
            ad_name=r.ad_name,
            campaign_name=r.campaign_name,
            offer_code=r.offer_code,
            is_active=r.is_active,
            spend=_fmt_decimal(r.spend),
            leads=r.leads,
            deposits=r.deposits,
            last_alert_at=r.last_alert_at,
            last_alert_stage=r.last_alert_stage,
            last_disable_at=r.last_disable_at,
            alerts_count_in_window=r.alerts_count_in_window,
        )
        for r in rows
    ]

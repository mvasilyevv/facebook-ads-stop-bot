# -*- coding: utf-8 -*-
"""SQL-слой для HistoryPage endpoints — чистые async-функции запросов.

Вынесено из apps/api/routers/v1/history.py (был 743 строки, >500 design-rule).
Router теперь тонкий: валидация окна + вызов этих функций + маппинг в схемы.
Здесь — только SQL и выполнение, без зависимостей от FastAPI/Pydantic.

Все partitioned-таблицы фильтруются по partition-key (partition pruning):
    ad_metrics   → WHERE cycle_ts BETWEEN :from_dt AND :to_dt
    alert_events → WHERE created_at BETWEEN :from_dt AND :to_dt

CRIT-1: ad_metrics хранит КУМУЛЯТИВНЫЕ snapshot'ы (spend растёт за сутки и
сбрасывается посуточно). Везде, где суммируются метрики за период, используется
latest_per_ad_per_day_cte (DISTINCT ON (ad_id, day) → SUM дневных итогов), а не
наивный SUM по всем cycle-строкам. См. core/dashboard/metric_aggregation.py.

matched_rule_codes — JSONB! Unnest через jsonb_array_elements_text().
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.dashboard.metric_aggregation import latest_per_ad_per_day_cte
from core.tasks.channel import disable_channel_sql, enable_channel_sql, target_id_sql

# ─────────────────────── Summary (5 запросов на одном conn) ───────────────────


async def fetch_summary_metrics(
    conn: AsyncConnection, from_dt: datetime, to_dt: datetime
) -> Row[Any]:
    """Суммы метрик за период — партиционированная ad_metrics (cycle_ts).

    CRIT-1: кумулятивные snapshot'ы, spend сбрасывается посуточно. Окно
    многодневное (до 90д) → latest-per-ad-per-day, затем SUM (дневные итоги).
    """
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
    return (await conn.execute(metrics_sql, {"from_dt": from_dt, "to_dt": to_dt})).one()


async def fetch_active_ads_count(conn: AsyncConnection) -> Row[Any]:
    """Количество активных объявлений (last_seen >= now - 7d)."""
    active_sql = text(
        """
        SELECT COUNT(*) AS cnt
        FROM fb_ads
        WHERE last_seen_at >= NOW() - INTERVAL '7 days'
          AND is_active = true
        """
    )
    return (await conn.execute(active_sql)).one()


async def fetch_alerts_by_stage(
    conn: AsyncConnection, from_dt: datetime, to_dt: datetime
) -> list[Row[Any]]:
    """Алерты по stage — партиционированная alert_events (created_at)."""
    alerts_sql = text(
        """
        SELECT stage, COUNT(*) AS cnt
        FROM alert_events
        WHERE created_at BETWEEN :from_dt AND :to_dt
        GROUP BY stage
        """
    )
    return (await conn.execute(alerts_sql, {"from_dt": from_dt, "to_dt": to_dt})).fetchall()


async def fetch_rules_breakdown(
    conn: AsyncConnection, from_dt: datetime, to_dt: datetime
) -> list[Row[Any]]:
    """Срабатывания по правилам — unnest JSONB matched_rule_codes."""
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
    return (await conn.execute(rules_sql, {"from_dt": from_dt, "to_dt": to_dt})).fetchall()


async def fetch_tasks_summary(
    conn: AsyncConnection, from_dt: datetime, to_dt: datetime
) -> Row[Any]:
    """Задачи (disable/enable) — task_queue по updated_at (не партиционирована).

    Канал после удаления DOM — meta_api_mutation pause_ad/activate_ad (+ legacy).
    """
    disable_pred = disable_channel_sql("task_queue")
    enable_pred = enable_channel_sql("task_queue")
    tasks_sql = text(
        f"""
        SELECT
            COUNT(*) FILTER (WHERE {disable_pred} AND status = 'succeeded') AS disable_completed,
            COUNT(*) FILTER (WHERE {disable_pred} AND status = 'failed')    AS disable_failed,
            COUNT(*) FILTER (WHERE {enable_pred}  AND status = 'succeeded') AS enable_completed
        FROM task_queue
        WHERE ({disable_pred} OR {enable_pred})
          AND updated_at BETWEEN :from_dt AND :to_dt
        """
    )
    return (await conn.execute(tasks_sql, {"from_dt": from_dt, "to_dt": to_dt})).one()


# ─────────────────────── Timeline ────────────────────────────────────────────


async def fetch_timeline(
    engine: AsyncEngine, from_dt: datetime, to_dt: datetime, limit: int
) -> list[Row[Any]]:
    """Объединённая лента: AlertEvent + terminal TaskQueue (succeeded/failed/cancelled).

    UNION ALL с JOIN FbAd→FbAdset→FbCampaign для имён. Сорт по ts DESC.
    """
    target_expr = target_id_sql("tq")
    toggle_pred = f"({disable_channel_sql('tq')} OR {enable_channel_sql('tq')})"
    sql = text(
        f"""
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
            {target_expr}       AS fb_ad_id,
            a.ad_name,
            c.campaign_name,
            NULL                AS stage,
            NULL                AS rule_codes_raw,
            tq.task_type,
            tq.status           AS task_status
        FROM task_queue tq
        LEFT JOIN fb_ads a    ON a.fb_ad_id = {target_expr}
        LEFT JOIN fb_adsets s ON s.id = a.adset_id
        LEFT JOIN fb_campaigns c ON c.id = s.campaign_id
        WHERE {toggle_pred}
          AND tq.status IN ('succeeded', 'failed', 'cancelled')
          AND tq.updated_at BETWEEN :from_dt AND :to_dt

        ORDER BY ts DESC
        LIMIT :limit
        """
    )
    async with engine.connect() as conn:
        return (
            await conn.execute(sql, {"from_dt": from_dt, "to_dt": to_dt, "limit": limit})
        ).fetchall()


# ─────────────────────── Campaigns ───────────────────────────────────────────


async def fetch_campaigns(
    engine: AsyncEngine, from_dt: datetime, to_dt: datetime, limit: int
) -> list[Row[Any]]:
    """GROUP BY кампании: spend/leads/deps + active_ads_count + alerts_count.

    CRIT-1: ad_metrics кумулятивна, spend сбрасывается посуточно. Окно до 90д →
    per-ad-per-day latest, затем SUM до per-ad-итога в CTE per_ad. Внешний SUM
    по кампании складывает per-ad-итоги (каждый ad джойнится один раз).
    """
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
        return (
            await conn.execute(sql, {"from_dt": from_dt, "to_dt": to_dt, "limit": limit})
        ).fetchall()


# ─────────────────────── Events ──────────────────────────────────────────────


async def fetch_events(
    engine: AsyncEngine,
    from_dt: datetime,
    to_dt: datetime,
    *,
    campaign_uuid: uuid.UUID | None,
    fb_ad_id: str | None,
    stage: str | None,
    limit: int,
) -> list[Row[Any]]:
    """AlertEvent drill-down с JOIN ad/adset/campaign/offer.

    Опциональные фильтры campaign_id/fb_ad_id/stage подставляются как WHERE-блоки.
    """
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

    params: dict[str, Any] = {"from_dt": from_dt, "to_dt": to_dt, "limit": limit}
    if campaign_uuid:
        params["campaign_uuid"] = campaign_uuid
    if fb_ad_id:
        params["fb_ad_id"] = fb_ad_id
    if stage:
        params["stage"] = stage

    async with engine.connect() as conn:
        return (await conn.execute(sql, params)).fetchall()


# ─────────────────────── Offers ──────────────────────────────────────────────


async def fetch_offers(
    engine: AsyncEngine, from_dt: datetime, to_dt: datetime, limit: int
) -> list[Row[Any]]:
    """GROUP BY Offer через JOIN fb_campaigns.offer_id.

    CRIT-1: per-ad-per-day latest → per-ad SUM в CTE, чтобы spend не завышался
    кумулятивом и посуточным reset'ом. alert_events джойнится отдельно (count).
    """
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
        return (
            await conn.execute(sql, {"from_dt": from_dt, "to_dt": to_dt, "limit": limit})
        ).fetchall()


# ─────────────────────── Ads ─────────────────────────────────────────────────


async def fetch_ads(
    engine: AsyncEngine,
    from_dt: datetime,
    to_dt: datetime,
    *,
    campaign_uuid: uuid.UUID | None,
    offer_uuid: uuid.UUID | None,
    limit: int,
) -> list[Row[Any]]:
    """GROUP BY объявлению: spend/leads/deps + last_alert + last_disable.

    last_alert_at / last_alert_stage — через LATERAL subquery.
    last_disable_at — через LATERAL subquery по task_queue.

    CRIT-1: per-ad-per-day latest → per-ad SUM, иначе кумулятив + посуточный
    reset завышают spend. Внешний SUM по a.id берёт единственную per-ad строку.
    """
    campaign_filter = "AND c.id = :campaign_uuid" if campaign_uuid else ""
    offer_filter = "AND o.id = :offer_uuid" if offer_uuid else ""

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
            WHERE {disable_channel_sql("tq")}
              AND tq.status = 'succeeded'
              AND {target_id_sql("tq")} = a.fb_ad_id
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

    params: dict[str, Any] = {"from_dt": from_dt, "to_dt": to_dt, "limit": limit}
    if campaign_uuid:
        params["campaign_uuid"] = campaign_uuid
    if offer_uuid:
        params["offer_uuid"] = offer_uuid

    async with engine.connect() as conn:
        return (await conn.execute(sql, params)).fetchall()


__all__ = [
    "fetch_summary_metrics",
    "fetch_active_ads_count",
    "fetch_alerts_by_stage",
    "fetch_rules_breakdown",
    "fetch_tasks_summary",
    "fetch_timeline",
    "fetch_campaigns",
    "fetch_events",
    "fetch_offers",
    "fetch_ads",
]

# -*- coding: utf-8 -*-
"""Роутер dashboard-performance: топ кампаний, leaderboard офферов, rule violations.

Endpoint (с prefix /api от auto-discovery):
    GET /dashboard/performance — 3 параллельных тяжёлых SQL через asyncio.gather.

Партиционные WHERE по cycle_ts (ad_metrics) и created_at (alert_events) —
обязательны для partition pruning.

Решения:
- top_campaigns / offer_leaderboard агрегируют ad_metrics за окно ?days;
  cost_per_lead считаем как SUM(spend)/NULLIF(SUM(leads),0) — устойчиво к нулю.
- top_rule_violations: UNNEST из alert_events.matched_rule_codes (JSONB array)
  через jsonb_array_elements_text + GROUP BY.
- Все три запроса бегут параллельно (asyncio.gather). Fail-policy: если один
  падает — пробрасываем (fail-all), потому что performance-секция должна быть
  цельной (фронт не отображает «пол-секции»).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.api.deps import DepEngine
from apps.api.routers.v1.schemas.dashboard_aggregates import (
    DashboardPerformanceOut,
    OfferLeaderboardRowOut,
    RuleViolationOut,
    TopCampaignOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])


def _decimal_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


async def _query_top_campaigns(
    engine: AsyncEngine, *, days: int, limit: int
) -> list[TopCampaignOut]:
    """Топ кампаний по spend за окно ?days.

    Считает: spend / leads / deposits / cost_per_lead / active_ads_count.
    Сортировка — SUM(spend) DESC NULLS LAST.
    Partition pruning через WHERE m.cycle_ts.
    """
    sql = """
        SELECT
            fc.id                AS campaign_id,
            fc.fb_campaign_id    AS fb_campaign_id,
            fc.campaign_name     AS campaign_name,
            SUM(m.spend)         AS spend,
            SUM(m.leads)         AS leads,
            SUM(m.deposits)      AS deposits,
            CASE
                WHEN SUM(m.leads) IS NULL OR SUM(m.leads) = 0 THEN NULL
                ELSE SUM(m.spend) / SUM(m.leads)
            END                  AS cost_per_lead,
            COUNT(DISTINCT fa.id) AS active_ads_count
        FROM fb_campaigns fc
        JOIN fb_adsets fas ON fas.campaign_id = fc.id
        JOIN fb_ads fa     ON fa.adset_id = fas.id
        JOIN ad_metrics m  ON m.ad_id = fa.id
        WHERE m.cycle_ts >= NOW() - make_interval(days => :days)
        GROUP BY fc.id, fc.fb_campaign_id, fc.campaign_name
        ORDER BY SUM(m.spend) DESC NULLS LAST
        LIMIT :lim
    """
    async with engine.connect() as conn:
        rows = (await conn.execute(text(sql), {"days": days, "lim": limit})).fetchall()

    return [
        TopCampaignOut(
            campaign_id=str(r.campaign_id),
            fb_campaign_id=r.fb_campaign_id,
            campaign_name=r.campaign_name,
            spend=_decimal_str(r.spend),
            leads=_int_or_none(r.leads),
            deposits=_int_or_none(r.deposits),
            cost_per_lead=_decimal_str(r.cost_per_lead),
            active_ads_count=int(r.active_ads_count or 0),
        )
        for r in rows
    ]


async def _query_offer_leaderboard(
    engine: AsyncEngine, *, days: int, limit: int
) -> list[OfferLeaderboardRowOut]:
    """Leaderboard офферов: SUM-метрики + count алертов.

    Метрики идут через JOIN ad_metrics (partitioned). alerts_count считается
    в отдельной CTE (по той же оконной фильтрации created_at). LEFT JOIN
    объединяет результаты — оффер без алертов получит 0.
    """
    sql = """
        WITH metrics_per_offer AS (
            SELECT
                o.id            AS offer_id,
                o.code          AS offer_code,
                o.name          AS offer_name,
                SUM(m.spend)    AS spend,
                SUM(m.leads)    AS leads,
                SUM(m.registrations) AS registrations,
                SUM(m.deposits)      AS deposits
            FROM offers o
            JOIN fb_campaigns fc ON fc.offer_id = o.id
            JOIN fb_adsets fas   ON fas.campaign_id = fc.id
            JOIN fb_ads fa       ON fa.adset_id = fas.id
            JOIN ad_metrics m    ON m.ad_id = fa.id
            WHERE m.cycle_ts >= NOW() - make_interval(days => :days)
            GROUP BY o.id, o.code, o.name
        ),
        alerts_per_offer AS (
            SELECT
                o.id AS offer_id,
                COUNT(*) AS alerts_count
            FROM offers o
            JOIN fb_campaigns fc ON fc.offer_id = o.id
            JOIN fb_adsets fas   ON fas.campaign_id = fc.id
            JOIN fb_ads fa       ON fa.adset_id = fas.id
            JOIN alert_events ae ON ae.ad_id = fa.id
            WHERE ae.created_at >= NOW() - make_interval(days => :days)
            GROUP BY o.id
        )
        SELECT
            mpo.offer_id,
            mpo.offer_code,
            mpo.offer_name,
            mpo.spend,
            mpo.leads,
            mpo.registrations,
            mpo.deposits,
            COALESCE(apo.alerts_count, 0) AS alerts_count
        FROM metrics_per_offer mpo
        LEFT JOIN alerts_per_offer apo ON apo.offer_id = mpo.offer_id
        ORDER BY mpo.spend DESC NULLS LAST
        LIMIT :lim
    """
    async with engine.connect() as conn:
        rows = (await conn.execute(text(sql), {"days": days, "lim": limit})).fetchall()

    return [
        OfferLeaderboardRowOut(
            offer_id=str(r.offer_id),
            offer_code=r.offer_code,
            offer_name=r.offer_name,
            spend=_decimal_str(r.spend),
            leads=_int_or_none(r.leads),
            registrations=_int_or_none(r.registrations),
            deposits=_int_or_none(r.deposits),
            alerts_count=int(r.alerts_count or 0),
        )
        for r in rows
    ]


async def _query_top_rule_violations(
    engine: AsyncEngine, *, days: int, limit: int
) -> list[RuleViolationOut]:
    """Топ правил по количеству сработок за окно ?days.

    UNNEST через jsonb_array_elements_text (matched_rule_codes — JSONB array,
    не TEXT[]). Partition pruning по created_at в alert_events.
    Возвращаем count = всего сработок и ads_count = COUNT DISTINCT ad_id.
    """
    sql = """
        SELECT
            rule_code,
            COUNT(*) AS cnt,
            COUNT(DISTINCT ad_id) AS ads_count
        FROM (
            SELECT
                ae.ad_id,
                jsonb_array_elements_text(ae.matched_rule_codes) AS rule_code
            FROM alert_events ae
            WHERE ae.created_at >= NOW() - make_interval(days => :days)
        ) AS expanded
        GROUP BY rule_code
        ORDER BY cnt DESC
        LIMIT :lim
    """
    async with engine.connect() as conn:
        rows = (await conn.execute(text(sql), {"days": days, "lim": limit})).fetchall()

    return [
        RuleViolationOut(
            rule_code=r.rule_code,
            count=int(r.cnt),
            ads_count=int(r.ads_count or 0),
        )
        for r in rows
    ]


# ─────────────────────── GET /dashboard/performance ──────────────────────────


@router.get("/dashboard/performance", response_model=DashboardPerformanceOut)
async def get_dashboard_performance(
    engine: DepEngine,
    days: int = Query(default=7, ge=1, le=30, description="Окно агрегации (дни)"),
    limit_campaigns: int = Query(default=10, ge=1, le=100),
    limit_offers: int = Query(default=10, ge=1, le=100),
    limit_rules: int = Query(default=10, ge=1, le=100),
) -> DashboardPerformanceOut:
    """Тяжёлая агрегация: top кампаний, leaderboard офферов, rule violations.

    Три параллельных SQL через asyncio.gather. Каждый запрос использует
    partition pruning (cycle_ts/created_at) для ad_metrics и alert_events.

    Fail-policy: если хотя бы один подзапрос падает — пробрасываем (fail-all),
    потому что секция должна быть согласованной.

    Производительность: партиционирование + INDEX на (ad_id, cycle_ts) для
    ad_metrics; ix_alert_events_ad_created для alert_events; JOIN'ы через FK.
    """
    top_camps, leaderboard, rules = await asyncio.gather(
        _query_top_campaigns(engine, days=days, limit=limit_campaigns),
        _query_offer_leaderboard(engine, days=days, limit=limit_offers),
        _query_top_rule_violations(engine, days=days, limit=limit_rules),
    )

    return DashboardPerformanceOut(
        top_campaigns=top_camps,
        offer_leaderboard=leaderboard,
        top_rule_violations=rules,
    )


__all__ = ["router"]

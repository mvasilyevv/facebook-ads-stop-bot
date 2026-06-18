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
from apps.api.utils.serialize import decimal_str, int_or_none

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])


async def _query_top_campaigns(
    engine: AsyncEngine, *, days: int, limit: int
) -> list[TopCampaignOut]:
    """Топ кампаний по spend за окно ?days.

    Считает: spend / leads / deposits / cost_per_lead / active_ads_count.
    Сортировка — SUM(spend) DESC NULLS LAST.
    Partition pruning через WHERE m.cycle_ts.

    CRIT-1: ad_metrics — кумулятивные snapshot'ы, spend сбрасывается посуточно.
    Окно многодневное (до 30д) → берём ПОСЛЕДНИЙ snapshot на (ad × сутки) через
    DISTINCT ON (latest-per-ad-per-day), затем SUM по кампании — корректное
    сложение дневных итогов через cabinet day reset. Наивный SUM завышал spend.
    """
    sql = """
        WITH per_ad_day AS (
            SELECT DISTINCT ON (m.ad_id, date_trunc('day', m.cycle_ts))
                m.ad_id,
                m.spend,
                m.leads,
                m.deposits
            FROM ad_metrics m
            WHERE m.cycle_ts >= NOW() - make_interval(days => :days)
            ORDER BY m.ad_id, date_trunc('day', m.cycle_ts), m.cycle_ts DESC
        )
        SELECT
            fc.id                AS campaign_id,
            fc.fb_campaign_id    AS fb_campaign_id,
            fc.campaign_name     AS campaign_name,
            SUM(pad.spend)       AS spend,
            SUM(pad.leads)       AS leads,
            SUM(pad.deposits)    AS deposits,
            CASE
                WHEN SUM(pad.leads) IS NULL OR SUM(pad.leads) = 0 THEN NULL
                ELSE SUM(pad.spend) / SUM(pad.leads)
            END                  AS cost_per_lead,
            -- L8: «активные» = is_active AND виделись за 7д (единая семантика с
            -- history/offers/compare и _count_active_ads_normal). Без FILTER это был
            -- COUNT всех ads с метриками за окно → расхождение между секциями дашборда.
            COUNT(DISTINCT fa.id) FILTER (
                WHERE fa.is_active AND fa.last_seen_at >= NOW() - INTERVAL '7 days'
            ) AS active_ads_count
        FROM fb_campaigns fc
        JOIN fb_adsets fas ON fas.campaign_id = fc.id
        JOIN fb_ads fa     ON fa.adset_id = fas.id
        JOIN per_ad_day pad ON pad.ad_id = fa.id
        GROUP BY fc.id, fc.fb_campaign_id, fc.campaign_name
        ORDER BY SUM(pad.spend) DESC NULLS LAST
        LIMIT :lim
    """
    async with engine.connect() as conn:
        rows = (await conn.execute(text(sql), {"days": days, "lim": limit})).fetchall()

    return [
        TopCampaignOut(
            campaign_id=str(r.campaign_id),
            fb_campaign_id=r.fb_campaign_id,
            campaign_name=r.campaign_name,
            spend=decimal_str(r.spend),
            leads=int_or_none(r.leads),
            deposits=int_or_none(r.deposits),
            cost_per_lead=decimal_str(r.cost_per_lead),
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
        WITH per_ad_day AS (
            -- CRIT-1: latest-per-ad-per-day по кумулятивной ad_metrics (spend
            -- сбрасывается посуточно). SUM ниже складывает дневные итоги.
            SELECT DISTINCT ON (m.ad_id, date_trunc('day', m.cycle_ts))
                m.ad_id,
                m.spend,
                m.leads,
                m.registrations,
                m.deposits
            FROM ad_metrics m
            WHERE m.cycle_ts >= NOW() - make_interval(days => :days)
            ORDER BY m.ad_id, date_trunc('day', m.cycle_ts), m.cycle_ts DESC
        ),
        metrics_per_offer AS (
            SELECT
                o.id            AS offer_id,
                o.code          AS offer_code,
                o.name          AS offer_name,
                SUM(pad.spend)    AS spend,
                SUM(pad.leads)    AS leads,
                SUM(pad.registrations) AS registrations,
                SUM(pad.deposits)      AS deposits
            FROM offers o
            JOIN fb_campaigns fc ON fc.offer_id = o.id
            JOIN fb_adsets fas   ON fas.campaign_id = fc.id
            JOIN fb_ads fa       ON fa.adset_id = fas.id
            JOIN per_ad_day pad  ON pad.ad_id = fa.id
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
            spend=decimal_str(r.spend),
            leads=int_or_none(r.leads),
            registrations=int_or_none(r.registrations),
            deposits=int_or_none(r.deposits),
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

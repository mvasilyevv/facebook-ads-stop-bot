# -*- coding: utf-8 -*-
"""Роутер dashboard-timeseries: spend-history + chart-data.

Endpoints (с prefix /api от auto-discovery):
    GET /dashboard/spend-history — сырые точки ad_metrics (не агрегации).
    GET /dashboard/chart-data    — бакеты по hour|day (SUM + COUNT DISTINCT).

Партиционные WHERE по cycle_ts — обязательны для partition pruning.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from apps.api.deps import DepEngine
from apps.api.routers.v1.schemas.dashboard_aggregates import (
    ChartBucketOut,
    SpendPointOut,
)
from apps.api.utils.serialize import decimal_str, int_or_none

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

# Защита от перегруза JSON-ответа /spend-history без фильтра.
_SPEND_HISTORY_GLOBAL_LIMIT = 10000

# Допустимые значения бакета для chart-data.
_VALID_BUCKETS = {"hour", "day"}


# ─────────────────────── GET /dashboard/spend-history ────────────────────────


@router.get("/dashboard/spend-history", response_model=list[SpendPointOut])
async def get_spend_history(
    engine: DepEngine,
    hours: int = Query(default=24, ge=1, le=168, description="Окно в часах, max=168 (7d)"),
    fb_ad_id: str | None = Query(default=None, description="Фильтр по Meta ID ad'а"),
) -> list[dict[str, Any]]:
    """Сырые точки ad_metrics за окно hours.

    Партиционный фильтр по cycle_ts применяется в WHERE (partition pruning).
    Если fb_ad_id не передан — limit 10000 (защита от мегабайтных ответов).
    Если fb_ad_id передан — без limit (нас интересует история одного ad'а).
    ORDER BY cycle_ts ASC.
    """
    params: dict[str, Any] = {"hours": hours}
    where_clauses: list[str] = ["m.cycle_ts >= NOW() - make_interval(hours => :hours)"]

    if fb_ad_id:
        # Резолвим fb_ad_id → internal id одним JOIN'ом (без отдельного SELECT).
        where_clauses.append("fa.fb_ad_id = :fb_ad_id")
        params["fb_ad_id"] = fb_ad_id

    where_sql = " AND ".join(where_clauses)

    # Без фильтра — отрезаем 10k точек: без этого full scan может затянуть API.
    limit_clause = ""
    if not fb_ad_id:
        params["lim"] = _SPEND_HISTORY_GLOBAL_LIMIT
        limit_clause = "LIMIT :lim"

    sql = f"""
        SELECT
            m.cycle_ts,
            fa.fb_ad_id,
            m.spend,
            m.impressions,
            m.clicks,
            m.leads,
            m.registrations,
            m.deposits
        FROM ad_metrics m
        JOIN fb_ads fa ON fa.id = m.ad_id
        WHERE {where_sql}
        ORDER BY m.cycle_ts ASC
        {limit_clause}
    """

    async with engine.connect() as conn:
        rows = (await conn.execute(text(sql), params)).fetchall()

    return [
        {
            "cycle_ts": r.cycle_ts,
            "fb_ad_id": r.fb_ad_id,
            "spend": decimal_str(r.spend),
            "impressions": int_or_none(r.impressions),
            "clicks": int_or_none(r.clicks),
            "leads": int_or_none(r.leads),
            "registrations": int_or_none(r.registrations),
            "deposits": int_or_none(r.deposits),
        }
        for r in rows
    ]


# ─────────────────────── GET /dashboard/chart-data ───────────────────────────


@router.get("/dashboard/chart-data", response_model=list[ChartBucketOut])
async def get_chart_data(
    engine: DepEngine,
    hours: int = Query(default=24, ge=1, le=720, description="Окно в часах, max=720 (30d)"),
    bucket: str = Query(default="hour", description="hour | day"),
) -> list[dict[str, Any]]:
    """Бакетированный график для DashboardPage.

    Бакет = `date_trunc(bucket, cycle_ts)`. SUM по spend/impressions/clicks/leads/
    registrations/deposits + COUNT DISTINCT ad_id для active_ads.

    Бакеты без метрик в окне не появляются (gap). Это согласовано с фронтом —
    Recharts сам обработает разрывы.

    Partition pruning через WHERE cycle_ts >= NOW() - make_interval(hours).
    """
    if bucket not in _VALID_BUCKETS:
        raise HTTPException(
            status_code=422,
            detail=f"bucket должен быть одним из: {sorted(_VALID_BUCKETS)}",
        )

    # CRIT-1: ad_metrics — кумулятивные snapshot'ы. Наивный SUM(spend) сложил бы
    # все промежуточные снимки внутри бакета и завысил spend в десятки раз.
    # Правильно: внутри бакета кумулятив монотонен → берём ПОСЛЕДНИЙ snapshot
    # на (бакет × ad) через DISTINCT ON, и только потом SUM по бакету.
    # date_trunc принимает строку. Подставляем безопасно (bucket предвалидирован).
    sql = f"""
        WITH per_bucket_ad AS (
            SELECT DISTINCT ON (date_trunc('{bucket}', m.cycle_ts), m.ad_id)
                date_trunc('{bucket}', m.cycle_ts) AS ts,
                m.ad_id,
                m.spend,
                m.impressions,
                m.clicks,
                m.leads,
                m.registrations,
                m.deposits
            FROM ad_metrics m
            WHERE m.cycle_ts >= NOW() - make_interval(hours => :hours)
            ORDER BY date_trunc('{bucket}', m.cycle_ts), m.ad_id, m.cycle_ts DESC
        )
        SELECT
            ts,
            SUM(spend) AS spend,
            SUM(impressions) AS impressions,
            SUM(clicks) AS clicks,
            SUM(leads) AS leads,
            SUM(registrations) AS registrations,
            SUM(deposits) AS deposits,
            COUNT(DISTINCT ad_id) AS active_ads
        FROM per_bucket_ad
        GROUP BY ts
        ORDER BY ts ASC
    """

    async with engine.connect() as conn:
        rows = (await conn.execute(text(sql), {"hours": hours})).fetchall()

    return [
        {
            "ts": r.ts,
            "spend": decimal_str(r.spend),
            "impressions": int_or_none(r.impressions),
            "clicks": int_or_none(r.clicks),
            "leads": int_or_none(r.leads),
            "registrations": int_or_none(r.registrations),
            "deposits": int_or_none(r.deposits),
            "active_ads": int_or_none(r.active_ads),
        }
        for r in rows
    ]


__all__ = ["router"]

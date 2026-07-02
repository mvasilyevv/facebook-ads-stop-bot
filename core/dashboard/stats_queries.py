# -*- coding: utf-8 -*-
"""SQL-слой «Статистики залива» (/api/stats/*).

Meta-метрики (`ad_metrics`) — КУМУЛЯТИВНЫЕ снимки за сутки кабинета: любая
агрегация идёт через CTE-хелперы core/dashboard/metric_aggregation.py
(latest-per-ad / latest-per-ad-per-day), naive SUM запрещён (CRIT-1).
Трекер (`tracker_aggregate`) — уже дневной агрегат per (ad × country × day,
UTC-день), читается простым SUM/GROUP BY.

Все функции — тонкий async-доступ к БД без Pydantic/FastAPI: роутер
apps/api/routers/v1/stats.py маппит результат в схемы, производные считает
core/dashboard/stats_derived.py.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.dashboard.metric_aggregation import (
    latest_per_ad_per_day_cte,
    latest_per_ad_window_cte,
)

# Метрики воронки Meta — единый список для тоталов/серий (см. stats_derived.FUNNEL_METRICS).
_METRIC_COLUMNS: tuple[str, ...] = (
    "spend",
    "impressions",
    "clicks",
    "leads",
    "registrations",
    "deposits",
)

# SUM-список для внешнего SELECT поверх latest-CTE: spend — Decimal, счётчики — int.
_SUM_SELECT = """
            COALESCE(SUM(spend), 0)              AS spend,
            COALESCE(SUM(impressions), 0)::bigint AS impressions,
            COALESCE(SUM(clicks), 0)::bigint      AS clicks,
            COALESCE(SUM(leads), 0)::bigint       AS leads,
            COALESCE(SUM(registrations), 0)::bigint AS registrations,
            COALESCE(SUM(deposits), 0)::bigint    AS deposits
"""

# Разрезы breakdown «за сегодня»: ключ/лейбл группировки.
BREAKDOWN_GROUPS: dict[str, dict[str, str]] = {
    "offer": {
        "key": "COALESCE(o.code, 'без оффера')",
        "label": "COALESCE(o.name, o.code, 'Без оффера')",
    },
    "campaign": {
        "key": "COALESCE(c.fb_campaign_id, c.id::text)",
        "label": "COALESCE(c.campaign_name, '—')",
    },
}


async def dominant_cabinet_day_start(engine: AsyncEngine, redis: Any) -> datetime:
    """Начало текущих суток ДОМИНИРУЮЩЕГО кабинета в UTC.

    Тот же паттерн, что `_dominant_cabinet_day_start` в dashboard_timeseries:
    глобальное окно «сегодня» — одно; для мульти-кабинета берём оффсет первого
    известного кабинета (для одно-кабинетного кейса — точно), фолбэк — UTC-полночь.
    Ограничение мульти-TZ осознанное и задокументированное.
    """
    from core.dashboard.cabinet_spend import cabinet_day_start_utc
    from core.meta_api.account_tz import (
        DEFAULT_OFFSET_HOURS,
        active_account_ids,
        load_offset_map,
    )

    account_ids = await active_account_ids(engine)
    tz_map = await load_offset_map(redis, account_ids) if account_ids else {}
    offset = next(iter(tz_map.values()), DEFAULT_OFFSET_HOURS)
    return cabinet_day_start_utc(offset, datetime.now(UTC))


async def fetch_window_totals(
    engine: AsyncEngine, *, from_dt: datetime, to_dt: datetime
) -> dict[str, Any]:
    """Тоталы воронки за окно ОДНИХ суток кабинета (latest-per-ad → SUM).

    Окно обязано лежать внутри одних суток кабинета (from_dt = cabinet_day_start),
    иначе теряются дневные итоги до посуточного reset'а — для многодневных окон
    используй fetch_period_totals.
    """
    sql = f"""
        WITH {latest_per_ad_window_cte(cte_alias="latest_per_ad", columns=_METRIC_COLUMNS)}
        SELECT {_SUM_SELECT}
        FROM latest_per_ad
    """
    async with engine.connect() as conn:
        row = (await conn.execute(text(sql), {"from_dt": from_dt, "to_dt": to_dt})).one()
    return dict(row._mapping)


async def fetch_hourly_snapshot_rows(
    engine: AsyncEngine, *, from_dt: datetime, to_dt: datetime
) -> list[dict[str, Any]]:
    """Последние снимки на (час × ad) за окно — сырьё для честных дельт.

    Дельты «сколько в этот час» считает stats_derived.hourly_deltas (LAG per-ad
    в Python): объём мал (объявления × 24 часа), а формула живёт в одном месте
    и покрыта unit-тестами без БД.
    """
    cte = latest_per_ad_window_cte(
        cte_alias="per_hour_ad",
        columns=_METRIC_COLUMNS,
        extra_select=", date_trunc('hour', m.cycle_ts) AS bucket_ts",
        bucket_expr="date_trunc('hour', m.cycle_ts)",
    )
    sql = f"""
        WITH {cte}
        SELECT ad_id, bucket_ts, spend, impressions, clicks, leads, registrations, deposits
        FROM per_hour_ad
        ORDER BY bucket_ts ASC
    """
    async with engine.connect() as conn:
        rows = (await conn.execute(text(sql), {"from_dt": from_dt, "to_dt": to_dt})).fetchall()
    return [dict(r._mapping) for r in rows]


async def fetch_period_totals(
    engine: AsyncEngine, *, from_dt: datetime, to_dt: datetime
) -> dict[str, Any]:
    """Тоталы воронки за многодневный период (latest-per-ad-PER-DAY → SUM).

    Дневные итоги складываются через посуточные сбросы кумулятива —
    эталон: core/dashboard/history_queries.fetch_summary_metrics.
    """
    sql = f"""
        WITH {latest_per_ad_per_day_cte(cte_alias="per_ad_day", columns=_METRIC_COLUMNS)}
        SELECT {_SUM_SELECT}
        FROM per_ad_day
    """
    async with engine.connect() as conn:
        row = (await conn.execute(text(sql), {"from_dt": from_dt, "to_dt": to_dt})).one()
    return dict(row._mapping)


async def fetch_daily_series(
    engine: AsyncEngine, *, from_dt: datetime, to_dt: datetime
) -> list[dict[str, Any]]:
    """Подневная серия воронки за период: latest-per-ad-per-day → SUM GROUP BY day.

    Дельты не нужны: latest-снимок дня и есть дневной итог объявления.
    День — календарный UTC (`date_trunc('day', cycle_ts)`), как во всей
    history-аналитике; расхождение с сутками кабинета задокументировано.
    """
    cte = latest_per_ad_per_day_cte(
        cte_alias="per_ad_day",
        columns=_METRIC_COLUMNS,
        extra_select=", date_trunc('day', m.cycle_ts) AS day_bucket",
    )
    sql = f"""
        WITH {cte}
        SELECT
            day_bucket::date AS day,
            {_SUM_SELECT},
            COUNT(DISTINCT ad_id)::int AS active_ads
        FROM per_ad_day
        GROUP BY day_bucket
        ORDER BY day_bucket ASC
    """
    async with engine.connect() as conn:
        rows = (await conn.execute(text(sql), {"from_dt": from_dt, "to_dt": to_dt})).fetchall()
    return [dict(r._mapping) for r in rows]


async def fetch_tracker_totals(
    engine: AsyncEngine, *, day_from: date, day_to: date
) -> dict[str, Any]:
    """Тоталы трекера (AdSet.pro) за диапазон UTC-дней. Не кумулятив — простой SUM."""
    sql = """
        SELECT
            COALESCE(SUM(installs), 0)::int      AS installs,
            COALESCE(SUM(registrations), 0)::int AS registrations,
            COALESCE(SUM(deposits), 0)::int      AS deposits,
            COALESCE(SUM(revenue), 0)            AS revenue
        FROM tracker_aggregate
        WHERE day BETWEEN :day_from AND :day_to
    """
    async with engine.connect() as conn:
        row = (await conn.execute(text(sql), {"day_from": day_from, "day_to": day_to})).one()
    return dict(row._mapping)


async def fetch_tracker_daily(
    engine: AsyncEngine, *, day_from: date, day_to: date
) -> list[dict[str, Any]]:
    """Подневная серия трекера за диапазон UTC-дней."""
    sql = """
        SELECT
            day,
            COALESCE(SUM(installs), 0)::int      AS installs,
            COALESCE(SUM(registrations), 0)::int AS registrations,
            COALESCE(SUM(deposits), 0)::int      AS deposits,
            COALESCE(SUM(revenue), 0)            AS revenue
        FROM tracker_aggregate
        WHERE day BETWEEN :day_from AND :day_to
        GROUP BY day
        ORDER BY day ASC
    """
    async with engine.connect() as conn:
        rows = (await conn.execute(text(sql), {"day_from": day_from, "day_to": day_to})).fetchall()
    return [dict(r._mapping) for r in rows]


async def fetch_breakdown(
    engine: AsyncEngine,
    *,
    from_dt: datetime,
    to_dt: datetime,
    group: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Разрез «за сегодня» по офферу/кампании поверх того же latest-per-ad CTE.

    group ∈ BREAKDOWN_GROUPS (валидируется в роутере). Окно — одни сутки кабинета
    (как fetch_window_totals). Сортировка по spend DESC.
    """
    exprs = BREAKDOWN_GROUPS[group]
    cte = latest_per_ad_window_cte(
        cte_alias="latest_per_ad",
        columns=("spend", "clicks", "leads", "registrations", "deposits"),
    )
    sql = f"""
        WITH {cte}
        SELECT
            {exprs["key"]}   AS key,
            {exprs["label"]} AS label,
            COALESCE(SUM(l.spend), 0)               AS spend,
            COALESCE(SUM(l.clicks), 0)::bigint      AS clicks,
            COALESCE(SUM(l.leads), 0)::bigint       AS leads,
            COALESCE(SUM(l.registrations), 0)::bigint AS registrations,
            COALESCE(SUM(l.deposits), 0)::bigint    AS deposits
        FROM latest_per_ad l
        JOIN fb_ads a        ON a.id = l.ad_id
        JOIN fb_adsets s     ON s.id = a.adset_id
        JOIN fb_campaigns c  ON c.id = s.campaign_id
        LEFT JOIN offers o   ON o.id = c.offer_id
        GROUP BY {exprs["key"]}, {exprs["label"]}
        ORDER BY COALESCE(SUM(l.spend), 0) DESC
        LIMIT :lim
    """
    async with engine.connect() as conn:
        rows = (
            await conn.execute(text(sql), {"from_dt": from_dt, "to_dt": to_dt, "lim": limit})
        ).fetchall()
    return [dict(r._mapping) for r in rows]


__all__ = [
    "BREAKDOWN_GROUPS",
    "dominant_cabinet_day_start",
    "fetch_breakdown",
    "fetch_daily_series",
    "fetch_hourly_snapshot_rows",
    "fetch_period_totals",
    "fetch_tracker_daily",
    "fetch_tracker_totals",
    "fetch_window_totals",
]

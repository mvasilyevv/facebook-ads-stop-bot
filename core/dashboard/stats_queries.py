"""Historical daily series for the operator snapshot.

Meta metrics are cumulative within a cabinet day, so each daily bucket first
selects the latest row per ad and only then aggregates. Naive SUM is forbidden.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.dashboard.metric_aggregation import latest_per_ad_per_day_cte

_METRIC_COLUMNS: tuple[str, ...] = (
    "spend",
    "impressions",
    "clicks",
    "leads",
    "registrations",
    "deposits",
)

_SUM_SELECT = """
            CASE WHEN COUNT(*) FILTER (WHERE spend IS NULL) = 0
                 THEN SUM(spend) END AS spend,
            CASE WHEN COUNT(*) FILTER (WHERE impressions IS NULL) = 0
                 THEN SUM(impressions)::bigint END AS impressions,
            CASE WHEN COUNT(*) FILTER (WHERE clicks IS NULL) = 0
                 THEN SUM(clicks)::bigint END AS clicks,
            CASE WHEN COUNT(*) FILTER (WHERE leads IS NULL) = 0
                 THEN SUM(leads)::bigint END AS leads,
            CASE WHEN COUNT(*) FILTER (WHERE registrations IS NULL) = 0
                 THEN SUM(registrations)::bigint END AS registrations,
            CASE WHEN COUNT(*) FILTER (WHERE deposits IS NULL) = 0
                 THEN SUM(deposits)::bigint END AS deposits
"""


async def fetch_daily_series(
    engine: AsyncEngine,
    *,
    from_dt: datetime,
    to_dt: datetime,
    account_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return latest-per-ad-per-cabinet-day totals grouped by persisted day."""
    cte = latest_per_ad_per_day_cte(
        cte_alias="per_ad_day",
        columns=_METRIC_COLUMNS,
    )
    sql = f"""
        WITH {cte}
        SELECT
            day_bucket::date AS day,
            {_SUM_SELECT},
            COUNT(DISTINCT ad_id)::int AS active_ads
        FROM per_ad_day
        WHERE (:account_id IS NULL OR ad_account_id = :account_id)
        GROUP BY day_bucket
        ORDER BY day_bucket ASC
    """
    canonical_account_id = account_id.removeprefix("act_") if account_id else None
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(sql),
                {
                    "from_dt": from_dt,
                    "to_dt": to_dt,
                    "account_id": canonical_account_id,
                },
            )
        ).fetchall()
    return [dict(row._mapping) for row in rows]


__all__ = ["fetch_daily_series"]

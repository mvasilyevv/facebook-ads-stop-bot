# -*- coding: utf-8 -*-
"""Idempotent reconciliation of live click state into daily tracker aggregates."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

REGISTRATION_EVENT_TYPES: tuple[str, ...] = ("registration",)
INSTALL_EVENT_TYPES: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class AggregationResult:
    window_start: datetime
    window_end: datetime
    day_floor: datetime
    day_ceil: datetime
    rows_upserted: int
    rows_inserted: int
    rows_updated: int
    deposits_total: int
    revenue_total: Decimal
    rows_dropped_invalid_country: int = 0


def _utc_day_bounds(window_start: datetime, window_end: datetime) -> tuple[datetime, datetime]:
    start = window_start.astimezone(timezone.utc)
    end = window_end.astimezone(timezone.utc)
    if end < start:
        raise ValueError(f"window_end ({end}) < window_start ({start})")
    day_floor = start.replace(hour=0, minute=0, second=0, microsecond=0)
    day_ceil = end.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return day_floor, day_ceil


_AGGREGATE_SQL = text(
    """
    WITH state_facts AS (
        SELECT ad_id, fb_ad_id, COALESCE(country, 'XX') AS country,
               registration_at AS occurred_at, 1 AS registrations, 0 AS ftds,
               0 AS confirmed_deposits, 0 AS redeposits, 0::numeric AS revenue,
               last_event_at
        FROM tracker_click_state
        WHERE ad_id IS NOT NULL AND registration_at IS NOT NULL
        UNION ALL
        SELECT ad_id, fb_ad_id, COALESCE(country, 'XX'), ftd_at,
               0, 1, 0, 0,
               CASE WHEN confirmed_deposit THEN ftd_revenue ELSE 0 END,
               last_event_at
        FROM tracker_click_state
        WHERE ad_id IS NOT NULL AND ftd_at IS NOT NULL
        UNION ALL
        SELECT ad_id, fb_ad_id, COALESCE(country, 'XX'), confirmed_deposit_at,
               0, 0, 1, 0, 0::numeric, last_event_at
        FROM tracker_click_state
        WHERE ad_id IS NOT NULL AND confirmed_deposit_at IS NOT NULL
    ),
    redeposit_facts AS (
        SELECT e.fb_ad_fk AS ad_id, e.fb_ad_id,
               CASE
                   WHEN char_length(UPPER(COALESCE(
                       e.raw_json->>'country', e.raw_json->>'country_code', e.raw_json->>'geo'
                   ))) = 2
                   THEN UPPER(COALESCE(
                       e.raw_json->>'country', e.raw_json->>'country_code', e.raw_json->>'geo'
                   ))
                   ELSE 'XX'
               END AS country,
               e.occurred_at, 0 AS registrations, 0 AS ftds,
               0 AS confirmed_deposits, 1 AS redeposits,
               COALESCE(e.revenue, 0) AS revenue, e.received_at AS last_event_at
        FROM adsetpro_postback_events e
        WHERE e.event_type = 'redeposit'
          AND e.provider_event_id IS NOT NULL
          AND e.fb_ad_fk IS NOT NULL
          AND e.is_duplicate = FALSE
          AND e.attribution_status <> 'ambiguous'
          AND e.received_at >= :partition_floor
          AND e.received_at < :partition_ceil
    ),
    facts AS (
        SELECT * FROM state_facts
        UNION ALL
        SELECT * FROM redeposit_facts
    ),
    filtered AS (
        SELECT ad_id, country, (occurred_at AT TIME ZONE 'UTC')::date AS day,
               registrations, ftds, confirmed_deposits, redeposits, revenue,
               last_event_at
        FROM facts
        WHERE occurred_at >= :day_floor AND occurred_at < :day_ceil
          AND (:all_ads OR fb_ad_id = ANY(:fb_ad_ids))
    )
    INSERT INTO tracker_aggregate
        (id, ad_id, country, day, installs, registrations, ftds, deposits,
         confirmed_deposits, redeposits, revenue, last_postback_at,
         created_at, updated_at)
    SELECT gen_random_uuid(), ad_id, country, day, 0,
           SUM(registrations)::int, SUM(ftds)::int,
           SUM(confirmed_deposits)::int, SUM(confirmed_deposits)::int,
           SUM(redeposits)::int, COALESCE(SUM(revenue), 0),
           MAX(last_event_at), now(), now()
    FROM filtered
    GROUP BY ad_id, country, day
    ON CONFLICT ON CONSTRAINT uq_tracker_aggregate_ad_country_day DO UPDATE SET
        installs = 0,
        registrations = EXCLUDED.registrations,
        ftds = EXCLUDED.ftds,
        deposits = EXCLUDED.confirmed_deposits,
        confirmed_deposits = EXCLUDED.confirmed_deposits,
        redeposits = EXCLUDED.redeposits,
        revenue = EXCLUDED.revenue,
        last_postback_at = EXCLUDED.last_postback_at,
        updated_at = now()
    RETURNING (xmax = 0) AS inserted, confirmed_deposits, revenue
    """
)

_INVALID_COUNTRY_COUNT_SQL = text(
    """
    SELECT COUNT(*)
    FROM tracker_click_state
    WHERE ad_id IS NOT NULL
      AND country IS NULL
      AND last_event_at >= :day_floor
      AND last_event_at < :day_ceil
      AND (:all_ads OR fb_ad_id = ANY(:fb_ad_ids))
    """
)


async def aggregate_postback_events(
    engine: AsyncEngine,
    *,
    window_start: datetime,
    window_end: datetime,
    deposit_event_types: tuple[str, ...] = ("ftd",),
    registration_event_types: tuple[str, ...] = REGISTRATION_EVENT_TYPES,
    install_event_types: tuple[str, ...] = INSTALL_EVENT_TYPES,
    fb_ad_ids: tuple[str, ...] | None = None,
) -> AggregationResult:
    """Recompute affected UTC days from the monotonic click projection.

    Legacy event-type parameters remain in the signature for compatibility; the
    canonical semantics are fixed: only registration + FTD on one click produces
    a confirmed deposit. ``fb_ad_ids`` enables immediate targeted aggregation.
    """
    del deposit_event_types, registration_event_types, install_event_types
    day_floor, day_ceil = _utc_day_bounds(window_start, window_end)
    selected_ads = tuple(fb_ad_ids or ())
    params = {
        "day_floor": day_floor,
        "day_ceil": day_ceil,
        # Received-at pruning may differ from occurred-at by delayed delivery.
        "partition_floor": day_floor - timedelta(days=2),
        "partition_ceil": day_ceil + timedelta(days=2),
        "all_ads": not selected_ads,
        "fb_ad_ids": list(selected_ads),
    }
    async with engine.begin() as conn:
        rows = (await conn.execute(_AGGREGATE_SQL, params)).all()
        invalid_country = int(
            (await conn.execute(_INVALID_COUNTRY_COUNT_SQL, params)).scalar() or 0
        )

    inserted = sum(1 for row in rows if row[0])
    result = AggregationResult(
        window_start=window_start,
        window_end=window_end,
        day_floor=day_floor,
        day_ceil=day_ceil,
        rows_upserted=len(rows),
        rows_inserted=inserted,
        rows_updated=len(rows) - inserted,
        deposits_total=sum(int(row[1] or 0) for row in rows),
        revenue_total=sum((Decimal(row[2] or 0) for row in rows), Decimal(0)),
        rows_dropped_invalid_country=invalid_country,
    )
    logger.info(
        "tracker_aggregate: days [%s..%s) ads=%s rows=%d confirmed=%d revenue=%s unmatched_country=%d",
        day_floor.date(),
        day_ceil.date(),
        selected_ads or "all",
        result.rows_upserted,
        result.deposits_total,
        result.revenue_total,
        invalid_country,
    )
    return result


async def aggregate_affected_event(
    engine: AsyncEngine,
    *,
    occurred_at: datetime,
    fb_ad_id: str,
) -> AggregationResult:
    """Fast path after one event; the periodic worker remains reconciliation."""
    return await aggregate_postback_events(
        engine,
        window_start=occurred_at,
        window_end=occurred_at,
        fb_ad_ids=(fb_ad_id,),
    )


__all__ = [
    "AggregationResult",
    "INSTALL_EVENT_TYPES",
    "REGISTRATION_EVENT_TYPES",
    "aggregate_affected_event",
    "aggregate_postback_events",
]

# -*- coding: utf-8 -*-
"""Fresh Meta snapshot gate for automatic money decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(slots=True, frozen=True)
class MetaSnapshotFreshness:
    fresh: bool
    latest_cycle_at: datetime | None
    interval_seconds: int


def snapshot_is_fresh(
    *,
    latest_cycle_at: datetime | None,
    interval_seconds: int,
    now: datetime,
) -> bool:
    """A money decision may use at most two observer intervals of staleness."""
    return bool(
        latest_cycle_at is not None
        and latest_cycle_at >= now - timedelta(seconds=max(interval_seconds, 1) * 2)
    )


async def load_meta_snapshot_freshness(
    engine: AsyncEngine,
    *,
    fb_ad_id: str,
    now: datetime | None = None,
) -> MetaSnapshotFreshness:
    """Load latest Meta metric timestamp and evaluate the two-interval gate."""
    checked_at = now or datetime.now(UTC)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT
                        (SELECT MAX(m.cycle_ts)
                         FROM ad_metrics m
                         JOIN fb_ads a ON a.id = m.ad_id
                         WHERE a.fb_ad_id = :fb_ad_id) AS latest_cycle,
                        COALESCE((SELECT interval_seconds FROM observer_config
                                  WHERE singleton_key = 'default' LIMIT 1), 90)
                            AS interval_seconds
                    """
                ),
                {"fb_ad_id": fb_ad_id},
            )
        ).one()
    latest_cycle = row[0]
    interval_seconds = int(row[1] or 90)
    return MetaSnapshotFreshness(
        fresh=snapshot_is_fresh(
            latest_cycle_at=latest_cycle,
            interval_seconds=interval_seconds,
            now=checked_at,
        ),
        latest_cycle_at=latest_cycle,
        interval_seconds=interval_seconds,
    )


async def defer_auto_stop_for_fresh_snapshot(
    engine: AsyncEngine,
    *,
    task_id: int,
    delay_seconds: int = 15,
) -> bool:
    """Return a claimed auto-stop to retry without consuming a failure attempt."""
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'retrying',
                    next_retry_at = now() + make_interval(secs => :delay_seconds),
                    external_started_at = NULL,
                    last_error = 'stale_meta_snapshot: observer refresh requested',
                    updated_at = now()
                WHERE id = :task_id
                  AND status = 'running'
                  AND external_started_at IS NULL
                """
            ),
            {"task_id": task_id, "delay_seconds": max(int(delay_seconds), 1)},
        )
    return bool(result.rowcount)


__all__ = [
    "MetaSnapshotFreshness",
    "defer_auto_stop_for_fresh_snapshot",
    "load_meta_snapshot_freshness",
    "snapshot_is_fresh",
]

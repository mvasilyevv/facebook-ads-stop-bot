# -*- coding: utf-8 -*-
"""Fresh Meta snapshot gate for automatic money decisions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.observer.scan_tasks import enqueue_observer_scan, observer_scan_idempotency_key


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
    lease_owner: uuid.UUID | None = None,
    lease_token: int | None = None,
) -> bool:
    """Return a claimed auto-stop to retry without consuming a failure attempt."""
    if lease_owner is None or lease_token is None or int(lease_token) <= 0:
        return False
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'retrying',
                    available_at = now() + make_interval(secs => :delay_seconds),
                    deadline_at = now() + make_interval(secs => :delay_seconds + 30),
                    external_started_at = NULL,
                    last_error = 'stale_meta_snapshot: observer refresh requested',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE id = :task_id
                  AND status = 'running'
                  AND external_started_at IS NULL
                  AND lease_owner = :lease_owner AND lease_token = :lease_token
                  AND lease_expires_at > clock_timestamp()
                """
            ),
            {
                "task_id": task_id,
                "delay_seconds": max(int(delay_seconds), 1),
                "lease_owner": lease_owner,
                "lease_token": lease_token,
            },
        )
        if result.rowcount:
            await enqueue_observer_scan(
                engine,
                requested_by="meta_api_worker",
                reason="auto_stop_requires_fresh_meta",
                idempotency_key=observer_scan_idempotency_key(
                    "auto-stop-refresh",
                    str(task_id),
                ),
                connection=conn,
            )
    return bool(result.rowcount)


__all__ = [
    "MetaSnapshotFreshness",
    "defer_auto_stop_for_fresh_snapshot",
    "load_meta_snapshot_freshness",
    "snapshot_is_fresh",
]

# -*- coding: utf-8 -*-
"""Fresh Meta snapshot gate for automatic money decisions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.observer.scan_tasks import enqueue_observer_scan, observer_scan_idempotency_key

MAX_AUTO_PAUSE_SNAPSHOT_AGE_SECONDS = 60


@dataclass(slots=True, frozen=True)
class MetaSnapshotFreshness:
    fresh: bool
    latest_cycle_at: datetime | None
    scan_id: int | None
    decision_confirmed: bool


def snapshot_is_fresh(
    *,
    latest_cycle_at: datetime | None,
    decision_confirmed: bool,
    now: datetime,
) -> bool:
    """Accept only a confirmed complete decision no more than 60 seconds old."""
    return bool(
        decision_confirmed
        and latest_cycle_at is not None
        and now - timedelta(seconds=MAX_AUTO_PAUSE_SNAPSHOT_AGE_SECONDS) <= latest_cycle_at <= now
    )


async def load_meta_snapshot_freshness(
    engine: AsyncEngine,
    *,
    fb_ad_id: str,
    now: datetime | None = None,
) -> MetaSnapshotFreshness:
    """Load the latest complete cabinet decision and enforce the 60-second gate."""
    checked_at = now or datetime.now(UTC)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    WITH latest_metric AS (
                        SELECT
                            m.cycle_ts,
                            m.scan_id,
                            m.currency,
                            a.id AS ad_id,
                            campaign.ad_account_id,
                            campaign.offer_id
                        FROM ad_metrics m
                        JOIN fb_ads a ON a.id = m.ad_id
                        JOIN fb_adsets adset ON adset.id = a.adset_id
                        JOIN fb_campaigns campaign ON campaign.id = adset.campaign_id
                        WHERE a.fb_ad_id = :fb_ad_id
                        ORDER BY m.cycle_ts DESC
                        LIMIT 1
                    )
                    SELECT
                        metric.cycle_ts,
                        metric.scan_id,
                        COALESCE(
                            metric.currency = 'USD'
                            AND scan.scan_id = metric.scan_id
                            AND scan.outcome = 'success'
                            AND scan.finished_at IS NOT NULL
                            AND scan.finished_at <= :checked_at
                            AND state.alert_state = 'stop_sent'
                            AND state.current_stage = 'stop'
                            AND state.last_scan_id = metric.scan_id
                            AND jsonb_array_length(state.stop_rule_codes) > 0
                            AND offer.is_active
                            AND rule.updated_at <= metric.cycle_ts
                            AND config.is_scanning_enabled,
                            FALSE
                        ) AS decision_confirmed
                    FROM latest_metric metric
                    LEFT JOIN LATERAL (
                        SELECT scan_id, outcome, finished_at
                        FROM scan_runs candidate
                        WHERE candidate.ad_account_id = metric.ad_account_id
                        ORDER BY candidate.started_at DESC, candidate.scan_id DESC
                        LIMIT 1
                    ) scan ON TRUE
                    LEFT JOIN ad_alert_state state ON state.ad_id = metric.ad_id
                    LEFT JOIN offers offer ON offer.id = metric.offer_id
                    LEFT JOIN offer_rules rule ON rule.offer_id = metric.offer_id
                    LEFT JOIN observer_config config ON config.singleton_key = 'default'
                    """
                ),
                {"fb_ad_id": fb_ad_id, "checked_at": checked_at},
            )
        ).one_or_none()
    latest_cycle = row[0] if row is not None else None
    scan_id = int(row[1]) if row is not None and row[1] is not None else None
    decision_confirmed = bool(row[2]) if row is not None else False
    return MetaSnapshotFreshness(
        fresh=snapshot_is_fresh(
            latest_cycle_at=latest_cycle,
            decision_confirmed=decision_confirmed,
            now=checked_at,
        ),
        latest_cycle_at=latest_cycle,
        scan_id=scan_id,
        decision_confirmed=decision_confirmed,
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
                    deadline_at = NULL,
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
    "MAX_AUTO_PAUSE_SNAPSHOT_AGE_SECONDS",
    "MetaSnapshotFreshness",
    "defer_auto_stop_for_fresh_snapshot",
    "load_meta_snapshot_freshness",
    "snapshot_is_fresh",
]

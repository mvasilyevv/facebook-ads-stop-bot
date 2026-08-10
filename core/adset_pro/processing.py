# -*- coding: utf-8 -*-
"""Durable processing and click-state projection for AdSet.pro events."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.adset_pro.ingest import (
    AttributionResult,
    resolve_attribution,
)
from core.adset_pro.schemas import PostbackEvent
from core.tasks.queue import transition_correlated_incident_in_transaction

_TASK_TYPE = "tracker_event_process"
_RETRY_BASE_SECONDS = 30
_RETRY_MAX_SECONDS = 300
_TRACKER_DEADLINE_SECONDS = 120
_TRACKER_LEASE_SECONDS = 120
_DEFAULT_TRACKER_WORKER_ID = uuid.uuid4()


@dataclass(slots=True, frozen=True)
class CancelResult:
    cancelled_task_ids: tuple[int, ...] = ()
    meta_snapshot_fresh: bool = False
    needs_scan_refresh: bool = False


@dataclass(slots=True, frozen=True)
class ProcessResult:
    task_id: int
    event_id: int | None
    processed: bool
    attribution_status: str
    fb_ad_id: str | None = None
    occurred_at: datetime | None = None
    received_at: datetime | None = None
    cancelled_task_ids: tuple[int, ...] = ()
    needs_scan_refresh: bool = False


@dataclass(slots=True, frozen=True)
class TrackerTaskClaim:
    """Opaque fencing capability returned by the tracker scheduler claim."""

    task_id: int
    lease_owner: uuid.UUID
    lease_token: int
    lease_expires_at: datetime
    deadline_at: datetime


class TrackerLeaseLostError(RuntimeError):
    """The tracker task is no longer owned by this worker generation."""


def _claim_params(claim: TrackerTaskClaim) -> dict[str, Any]:
    return {
        "task_id": int(claim.task_id),
        "lease_owner": claim.lease_owner,
        "lease_token": int(claim.lease_token),
    }


def confirmed_deposit_at(
    registration_at: datetime | None,
    ftd_at: datetime | None,
) -> datetime | None:
    """Confirmation time is order-independent and requires both events."""
    if registration_at is None or ftd_at is None:
        return None
    return max(registration_at, ftd_at)


def attribution_conflicts(
    candidate_ad_id: Any | None,
    existing_ad_ids: list[Any] | tuple[Any, ...],
) -> bool:
    """One provider-scoped click must never be projected onto multiple ads."""
    existing = {str(ad_id) for ad_id in existing_ad_ids if ad_id is not None}
    if len(existing) > 1:
        return True
    return candidate_ad_id is not None and bool(existing) and str(candidate_ad_id) not in existing


async def claim_event_tasks(
    engine: AsyncEngine,
    *,
    limit: int = 100,
    worker_id: uuid.UUID | None = None,
    lease_seconds: int = _TRACKER_LEASE_SECONDS,
) -> list[TrackerTaskClaim]:
    """Claim a fenced batch from the background lane.

    The returned owner/token pair is a capability: every later projection,
    retry, or terminal transition must present the same pair. A worker from an
    expired generation therefore cannot commit after another worker reclaims
    the row.
    """
    effective_worker_id = worker_id or _DEFAULT_TRACKER_WORKER_ID
    effective_lease_seconds = max(5, int(lease_seconds))
    async with engine.begin() as conn:
        # Older code left exhausted retrying rows permanently non-runnable. Move
        # them to the terminal dead-letter state before claiming fresh work.
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'failed',
                    completed_at = COALESCE(completed_at, now()),
                    last_error = COALESCE(last_error, 'tracker retry budget exhausted'),
                    updated_at = now()
                WHERE task_type = 'tracker_event_process'
                  AND status IN ('pending', 'retrying')
                  AND attempt_count >= max_attempts
                """
            )
        )
        rows = (
            await conn.execute(
                text(
                    """
                    WITH candidates AS (
                        SELECT id
                        FROM task_queue
                        WHERE task_type = 'tracker_event_process'
                          AND status IN ('pending', 'retrying')
                          AND lane = 'background'
                          AND available_at <= clock_timestamp()
                          AND (
                              deadline_at IS NULL
                              OR deadline_at > clock_timestamp()
                          )
                          AND cancel_requested_at IS NULL
                          AND attempt_count < max_attempts
                        ORDER BY priority DESC, available_at, created_at, id
                        FOR UPDATE SKIP LOCKED
                        LIMIT :limit
                    )
                    UPDATE task_queue q
                    SET status = 'running',
                        attempt_count = q.attempt_count + 1,
                        deadline_at = COALESCE(
                            q.deadline_at,
                            clock_timestamp() + make_interval(secs => :deadline_seconds)
                        ),
                        lease_owner = :worker_id,
                        lease_token = q.lease_token + 1,
                        lease_expires_at = clock_timestamp()
                            + make_interval(secs => :lease_seconds),
                        updated_at = now()
                    FROM candidates c
                    WHERE q.id = c.id
                    RETURNING q.id, q.lease_owner, q.lease_token,
                              q.lease_expires_at, q.deadline_at
                    """
                ),
                {
                    "limit": max(1, int(limit)),
                    "worker_id": effective_worker_id,
                    "lease_seconds": effective_lease_seconds,
                    "deadline_seconds": _TRACKER_DEADLINE_SECONDS,
                },
            )
        ).all()
    return [
        TrackerTaskClaim(
            task_id=int(row[0]),
            lease_owner=row[1],
            lease_token=int(row[2]),
            lease_expires_at=row[3],
            deadline_at=row[4],
        )
        for row in rows
    ]


async def process_event_task(
    engine: AsyncEngine,
    *,
    claim: TrackerTaskClaim,
) -> ProcessResult:
    """Project one claimed event and finish/retry it under the same fence."""
    task_id = claim.task_id
    async with engine.begin() as conn:
        task = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT id, status, payload, attempt_count, max_attempts,
                           cancel_requested_at, cancel_reason
                    FROM task_queue
                    WHERE id = :task_id AND task_type = 'tracker_event_process'
                      AND lease_owner = :lease_owner AND lease_token = :lease_token
                      AND lease_expires_at > clock_timestamp()
                    FOR UPDATE
                    """
                    ),
                    _claim_params(claim),
                )
            )
            .mappings()
            .first()
        )
        if task is None:
            raise TrackerLeaseLostError(
                f"tracker task {task_id} is no longer owned by "
                f"{claim.lease_owner}/{claim.lease_token}"
            )
        if task["status"] != "running":
            raise TrackerLeaseLostError(
                f"tracker task {task_id} has fenced status {task['status']}"
            )

        payload = (
            task["payload"] if isinstance(task["payload"], dict) else json.loads(task["payload"])
        )
        event_id = int(payload["event_id"])
        received_at = datetime.fromisoformat(str(payload["received_at"]).replace("Z", "+00:00"))
        if task["cancel_requested_at"] is not None:
            cancelled = await conn.execute(
                text(
                    """
                    UPDATE task_queue
                    SET status = 'cancelled',
                        completed_at = now(),
                        last_error = COALESCE(cancel_reason, 'cancelled'),
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        updated_at = now()
                    WHERE id = :task_id AND status = 'running'
                      AND lease_owner = :lease_owner AND lease_token = :lease_token
                      AND lease_expires_at > clock_timestamp()
                    """
                ),
                _claim_params(claim),
            )
            if not cancelled.rowcount:
                raise TrackerLeaseLostError(f"tracker task {task_id} lost fence during cancel")
            return ProcessResult(
                task_id=task_id,
                event_id=event_id,
                processed=False,
                attribution_status="cancelled",
                received_at=received_at,
            )
        event = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT id, received_at, occurred_at, source, provider_event_id,
                           click_id, fb_ad_id, fb_ad_fk, event_type, revenue,
                           currency, raw_json, signature_valid, attribution_status
                    FROM adsetpro_postback_events
                    WHERE id = :event_id AND received_at = :received_at
                    FOR UPDATE
                    """
                    ),
                    {"event_id": event_id, "received_at": received_at},
                )
            )
            .mappings()
            .first()
        )
        if event is None:
            await _finish_task_failed(conn, claim, "tracker event is missing")
            return ProcessResult(
                task_id=task_id,
                event_id=event_id,
                processed=False,
                attribution_status="missing_event",
            )

        event = dict(event)

        source = str(event["source"])
        click_id = str(event["click_id"])
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": f"tracker-state:{source}:{click_id}"},
        )

        attribution = await _resolve_event_attribution(conn, event)
        if attribution.ad_id is None:
            inherited = (
                await conn.execute(
                    text(
                        """
                        SELECT ad_id, fb_ad_id, attribution_status
                        FROM tracker_click_state
                        WHERE source = :source AND click_id = :click_id AND ad_id IS NOT NULL
                        LIMIT 1
                        """
                    ),
                    {"source": source, "click_id": click_id},
                )
            ).first()
            if inherited is not None:
                attribution = AttributionResult(
                    ad_id=inherited[0],
                    fb_ad_id=inherited[1],
                    status="matched_click",
                )

        existing_attribution = (
            await conn.execute(
                text(
                    """
                    SELECT DISTINCT ad_id, fb_ad_id
                    FROM (
                        SELECT fb_ad_fk AS ad_id, fb_ad_id
                        FROM adsetpro_postback_events
                        WHERE source = :source
                          AND click_id = :click_id
                          AND is_duplicate = FALSE
                          AND (fb_ad_fk IS NOT NULL OR fb_ad_id IS NOT NULL)
                          AND attribution_status <> 'ambiguous'
                        UNION
                        SELECT ad_id, fb_ad_id
                        FROM tracker_click_state
                        WHERE source = :source
                          AND click_id = :click_id
                          AND (ad_id IS NOT NULL OR fb_ad_id IS NOT NULL)
                    ) attributed
                    """
                ),
                {"source": source, "click_id": click_id},
            )
        ).all()
        existing_ad_ids = [row[0] for row in existing_attribution]
        existing_fb_ad_ids = [row[1] for row in existing_attribution]
        explicit_fb_ad_id = event["fb_ad_id"] or attribution.fb_ad_id
        if attribution_conflicts(attribution.ad_id, existing_ad_ids) or attribution_conflicts(
            explicit_fb_ad_id, existing_fb_ad_ids
        ):
            error = "conflicting_ad_attribution"
            await conn.execute(
                text(
                    """
                    UPDATE adsetpro_postback_events
                    SET attribution_status = 'ambiguous',
                        attempt_count = attempt_count + 1,
                        last_error = :error,
                        next_retry_at = NULL,
                        processed_at = now()
                    WHERE id = :event_id AND received_at = :received_at
                    """
                ),
                {"error": error, "event_id": event_id, "received_at": received_at},
            )
            await _finish_task_failed(conn, claim, error)
            return ProcessResult(
                task_id=task_id,
                event_id=event_id,
                processed=False,
                attribution_status="ambiguous",
                fb_ad_id=explicit_fb_ad_id,
                occurred_at=event["occurred_at"],
                received_at=event["received_at"],
            )

        if attribution.ad_id is not None:
            # One matched event is enough to attribute the other half of the same
            # click. This is safe because click_id is provider-scoped.
            await conn.execute(
                text(
                    """
                    UPDATE adsetpro_postback_events
                    SET fb_ad_fk = :ad_id,
                        fb_ad_id = :fb_ad_id,
                        attribution_status = CASE
                            WHEN id = :event_id AND received_at = :received_at
                                THEN :attribution_status
                            ELSE 'matched_click'
                        END
                    WHERE source = :source AND click_id = :click_id
                      AND fb_ad_fk IS NULL
                      AND attribution_status <> 'ambiguous'
                    """
                ),
                {
                    "ad_id": attribution.ad_id,
                    "fb_ad_id": attribution.fb_ad_id,
                    "event_id": event_id,
                    "received_at": received_at,
                    "attribution_status": attribution.status,
                    "source": source,
                    "click_id": click_id,
                },
            )

        state = await _rebuild_click_state(
            conn, source=source, click_id=click_id, attribution=attribution
        )
        cancel = CancelResult()
        if attribution.fb_ad_id and event["event_type"] in {"registration", "ftd"}:
            cancel = await cancel_unstarted_auto_pause(
                conn,
                fb_ad_id=attribution.fb_ad_id,
                now=datetime.now(UTC),
            )

        if attribution.ad_id is None:
            attempt_count = int(task["attempt_count"] or 1)
            max_attempts = int(task["max_attempts"] or 1)
            exhausted = attempt_count >= max_attempts
            retry_at = None if exhausted else _retry_at(attempt_count)
            error = f"attribution_{attribution.status}"
            await conn.execute(
                text(
                    """
                    UPDATE adsetpro_postback_events
                    SET attribution_status = :status,
                        attempt_count = attempt_count + 1,
                        last_error = :error,
                        next_retry_at = :retry_at
                    WHERE id = :event_id AND received_at = :received_at
                    """
                ),
                {
                    "status": attribution.status,
                    "error": error,
                    "retry_at": retry_at,
                    "event_id": event_id,
                    "received_at": received_at,
                },
            )
            if exhausted:
                await _finish_task_failed(conn, claim, error)
            else:
                retried = await conn.execute(
                    text(
                        """
                        UPDATE task_queue
                        SET status = 'retrying',
                            available_at = CAST(:retry_at AS TIMESTAMPTZ),
                            deadline_at = CAST(:retry_at AS TIMESTAMPTZ)
                                + make_interval(secs => :deadline_seconds),
                            last_error = :error,
                            lease_owner = NULL,
                            lease_expires_at = NULL,
                            updated_at = now()
                        WHERE id = :task_id AND status = 'running'
                          AND lease_owner = :lease_owner AND lease_token = :lease_token
                          AND lease_expires_at > clock_timestamp()
                        """
                    ),
                    {
                        **_claim_params(claim),
                        "retry_at": retry_at,
                        "deadline_seconds": _TRACKER_DEADLINE_SECONDS,
                        "error": error,
                    },
                )
                if not retried.rowcount:
                    raise TrackerLeaseLostError(
                        f"tracker task {task_id} lost fence while scheduling attribution retry"
                    )
            return ProcessResult(
                task_id=task_id,
                event_id=event_id,
                processed=False,
                attribution_status=attribution.status,
                fb_ad_id=attribution.fb_ad_id,
                occurred_at=event["occurred_at"],
                received_at=event["received_at"],
                needs_scan_refresh=True,
            )

        await conn.execute(
            text(
                """
                UPDATE adsetpro_postback_events
                SET fb_ad_fk = :ad_id, fb_ad_id = :fb_ad_id,
                    attribution_status = :status, attempt_count = attempt_count + 1,
                    last_error = NULL, next_retry_at = NULL, processed_at = now()
                WHERE id = :event_id AND received_at = :received_at
                """
            ),
            {
                "ad_id": attribution.ad_id,
                "fb_ad_id": attribution.fb_ad_id,
                "status": attribution.status,
                "event_id": event_id,
                "received_at": received_at,
            },
        )
        finished = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'succeeded', result = CAST(:result AS JSONB),
                    last_error = NULL,
                    completed_at = now(), updated_at = now()
                WHERE id = :task_id AND status = 'running'
                  AND lease_owner = :lease_owner AND lease_token = :lease_token
                  AND lease_expires_at > clock_timestamp()
                """
            ),
            {
                **_claim_params(claim),
                "result": json.dumps(
                    {
                        "event_id": event_id,
                        "source": source,
                        "click_id": click_id,
                        "state_version": state["version"],
                        "confirmed_deposit": state["confirmed_deposit"],
                        "cancelled_task_ids": list(cancel.cancelled_task_ids),
                    }
                ),
            },
        )
        if not finished.rowcount:
            raise TrackerLeaseLostError(f"tracker task {task_id} lost fence during finalize")
        return ProcessResult(
            task_id=task_id,
            event_id=event_id,
            processed=True,
            attribution_status=attribution.status,
            fb_ad_id=attribution.fb_ad_id,
            occurred_at=event["occurred_at"],
            received_at=event["received_at"],
            cancelled_task_ids=cancel.cancelled_task_ids,
            needs_scan_refresh=cancel.needs_scan_refresh,
        )


async def _resolve_event_attribution(
    conn: AsyncConnection,
    event: Any,
) -> AttributionResult:
    if event["fb_ad_fk"] is not None:
        return AttributionResult(
            ad_id=event["fb_ad_fk"],
            fb_ad_id=event["fb_ad_id"],
            status=str(event["attribution_status"] or "matched_direct"),
        )
    dto = PostbackEvent(
        click_id=str(event["click_id"]),
        fb_ad_id=event["fb_ad_id"],
        event_type=str(event["event_type"]),
        revenue=None if event["revenue"] is None else Decimal(event["revenue"]),
        currency=str(event["currency"]) if event["currency"] else None,
        received_at=event["received_at"],
        occurred_at=event["occurred_at"],
        source=str(event["source"]),
        provider_event_id=event["provider_event_id"],
        raw=dict(event["raw_json"] or {}),
    )
    return await resolve_attribution(conn, dto)


async def _rebuild_click_state(
    conn: AsyncConnection,
    *,
    source: str,
    click_id: str,
    attribution: AttributionResult,
) -> Any:
    aggregate = (
        (
            await conn.execute(
                text(
                    """
                WITH events AS (
                    SELECT id, received_at, occurred_at, raw_json, event_type
                    FROM adsetpro_postback_events
                    WHERE source = :source
                      AND click_id = :click_id
                      AND is_duplicate = FALSE
                      AND attribution_status <> 'ambiguous'
                      AND (
                          (CAST(:ad_id AS uuid) IS NULL AND fb_ad_fk IS NULL)
                          OR fb_ad_fk = CAST(:ad_id AS uuid)
                      )
                )
                SELECT
                    MIN(occurred_at) FILTER (
                        WHERE event_type = 'registration'
                    ) AS registration_at,
                    MIN(occurred_at) FILTER (WHERE event_type = 'ftd') AS ftd_at,
                    COUNT(*) FILTER (WHERE event_type = 'redeposit')::int AS redeposits,
                    MAX(received_at) AS last_event_at,
                    UPPER(COALESCE(
                        (ARRAY_AGG(raw_json->>'country' ORDER BY received_at DESC)
                            FILTER (WHERE raw_json->>'country' IS NOT NULL))[1],
                        (ARRAY_AGG(raw_json->>'country_code' ORDER BY received_at DESC)
                            FILTER (WHERE raw_json->>'country_code' IS NOT NULL))[1],
                        (ARRAY_AGG(raw_json->>'geo' ORDER BY received_at DESC)
                            FILTER (WHERE raw_json->>'geo' IS NOT NULL))[1]
                    )) AS country
                FROM events
                """
                ),
                {"source": source, "click_id": click_id, "ad_id": attribution.ad_id},
            )
        )
        .mappings()
        .one()
    )
    registration_at = aggregate["registration_at"]
    ftd_at = aggregate["ftd_at"]
    confirmed_at = confirmed_deposit_at(registration_at, ftd_at)
    country = aggregate["country"]
    if not country or len(country) != 2:
        country = None

    return (
        (
            await conn.execute(
                text(
                    """
                INSERT INTO tracker_click_state
                    (id, source, click_id, ad_id, fb_ad_id, country, attribution_status,
                     registration, ftd, confirmed_deposit, registration_at, ftd_at,
                     confirmed_deposit_at, redeposits,
                     last_event_at, version, created_at, updated_at)
                VALUES
                    (gen_random_uuid(), :source, :click_id, :ad_id, :fb_ad_id, :country,
                     :attribution_status, :registration, :ftd, :confirmed,
                     :registration_at, :ftd_at, :confirmed_at,
                     :redeposits, :last_event_at, 1, now(), now())
                ON CONFLICT ON CONSTRAINT uq_tracker_click_state_source_click DO UPDATE SET
                    ad_id = COALESCE(EXCLUDED.ad_id, tracker_click_state.ad_id),
                    fb_ad_id = COALESCE(EXCLUDED.fb_ad_id, tracker_click_state.fb_ad_id),
                    country = COALESCE(EXCLUDED.country, tracker_click_state.country),
                    attribution_status = CASE
                        WHEN COALESCE(EXCLUDED.ad_id, tracker_click_state.ad_id) IS NOT NULL
                            THEN EXCLUDED.attribution_status
                        ELSE tracker_click_state.attribution_status
                    END,
                    registration = tracker_click_state.registration OR EXCLUDED.registration,
                    ftd = tracker_click_state.ftd OR EXCLUDED.ftd,
                    confirmed_deposit = tracker_click_state.confirmed_deposit
                        OR EXCLUDED.confirmed_deposit,
                    registration_at = COALESCE(
                        LEAST(tracker_click_state.registration_at, EXCLUDED.registration_at),
                        tracker_click_state.registration_at, EXCLUDED.registration_at
                    ),
                    ftd_at = COALESCE(
                        LEAST(tracker_click_state.ftd_at, EXCLUDED.ftd_at),
                        tracker_click_state.ftd_at, EXCLUDED.ftd_at
                    ),
                    confirmed_deposit_at = COALESCE(
                        tracker_click_state.confirmed_deposit_at, EXCLUDED.confirmed_deposit_at
                    ),
                    redeposits = EXCLUDED.redeposits,
                    last_event_at = GREATEST(tracker_click_state.last_event_at, EXCLUDED.last_event_at),
                    version = tracker_click_state.version + 1,
                    updated_at = now()
                RETURNING version, confirmed_deposit
                """
                ),
                {
                    "source": source,
                    "click_id": click_id,
                    "ad_id": attribution.ad_id,
                    "fb_ad_id": attribution.fb_ad_id,
                    "country": country,
                    "attribution_status": attribution.status,
                    "registration": registration_at is not None,
                    "ftd": ftd_at is not None,
                    "confirmed": confirmed_at is not None,
                    "registration_at": registration_at,
                    "ftd_at": ftd_at,
                    "confirmed_at": confirmed_at,
                    "redeposits": aggregate["redeposits"],
                    "last_event_at": aggregate["last_event_at"],
                },
            )
        )
        .mappings()
        .one()
    )


async def cancel_unstarted_auto_pause(
    conn: AsyncConnection,
    *,
    fb_ad_id: str,
    now: datetime,
) -> CancelResult:
    """Cancel only fresh-snapshot, auto-stop pause tasks before external I/O."""
    await conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": fb_ad_id},
    )
    freshness = (
        await conn.execute(
            text(
                """
                SELECT
                    (SELECT MAX(m.cycle_ts)
                     FROM ad_metrics m
                     JOIN fb_ads a ON a.id = m.ad_id
                     WHERE a.fb_ad_id = :fb_ad_id) AS latest_cycle,
                    COALESCE((SELECT interval_seconds FROM observer_config
                              WHERE singleton_key = 'default' LIMIT 1), 90) AS interval_seconds
                """
            ),
            {"fb_ad_id": fb_ad_id},
        )
    ).first()
    latest_cycle = freshness[0] if freshness else None
    interval_seconds = int(freshness[1] if freshness else 90)
    is_fresh = bool(
        latest_cycle is not None
        and latest_cycle >= now - timedelta(seconds=max(interval_seconds, 1) * 2)
    )
    rows = (
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'cancelled',
                    last_error = 'cancelled_by_positive_tracker_event',
                    result = COALESCE(result, '{}'::jsonb)
                        || jsonb_build_object(
                            'outcome', 'REJECTED',
                            'reason', 'positive_tracker_event_before_external_call'
                        ),
                    completed_at = now(),
                    lease_owner = NULL, lease_expires_at = NULL,
                    updated_at = now()
                WHERE task_type = 'meta_api_mutation'
                  AND payload->>'mutation_kind' = 'pause_ad'
                  AND payload->>'target_id' = :fb_ad_id
                  AND requested_by = 'bot_auto_stop'
                  AND status IN ('pending', 'retrying', 'running')
                  AND external_started_at IS NULL
                RETURNING id, correlation_id, payload
                """
            ),
            {"fb_ad_id": fb_ad_id},
        )
    ).all()
    # If the tracker event wins after the conservative external boundary, leave
    # a durable cooperative cancellation marker. A proven pre-send rejection
    # takes this same target lock and terminally cancels; an ambiguous request
    # stays reconciliation-only.
    await conn.execute(
        text(
            """
            UPDATE task_queue
            SET cancel_requested_at = COALESCE(cancel_requested_at, now()),
                cancel_reason = COALESCE(
                    cancel_reason,
                    'positive_tracker_event_after_external_boundary'
                ),
                updated_at = now()
            WHERE task_type = 'meta_api_mutation'
              AND payload->>'mutation_kind' = 'pause_ad'
              AND payload->>'target_id' = :fb_ad_id
              AND requested_by = 'bot_auto_stop'
              AND status IN ('running', 'retrying')
              AND external_started_at IS NOT NULL
              AND cancel_requested_at IS NULL
            """
        ),
        {"fb_ad_id": fb_ad_id},
    )
    cancelled_task_ids: list[int] = []
    for row in rows:
        mapping = getattr(row, "_mapping", None)
        task_id = int(mapping["id"] if mapping is not None else row[0])
        cancelled_task_ids.append(task_id)
        raw_correlation_id = (
            mapping.get("correlation_id")
            if mapping is not None
            else (row[1] if len(row) > 1 else None)
        )
        correlation_id = (
            uuid.UUID(str(raw_correlation_id)) if raw_correlation_id is not None else None
        )
        payload = (
            mapping.get("payload") if mapping is not None else (row[2] if len(row) > 2 else None)
        )
        if isinstance(payload, str):
            payload = json.loads(payload)
        if correlation_id is not None:
            await transition_correlated_incident_in_transaction(
                conn,
                task_id=task_id,
                correlation_id=correlation_id,
                phase="recovered",
                payload=dict(payload or {}),
            )
    return CancelResult(
        cancelled_task_ids=tuple(cancelled_task_ids),
        meta_snapshot_fresh=is_fresh,
        needs_scan_refresh=not is_fresh,
    )


async def mark_task_retry(
    engine: AsyncEngine,
    *,
    claim: TrackerTaskClaim,
    error: str,
) -> bool:
    """Schedule an infra retry only while the caller still owns the lease."""
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT attempt_count, max_attempts, status, cancel_requested_at
                    FROM task_queue
                    WHERE id = :task_id AND task_type = 'tracker_event_process'
                      AND lease_owner = :lease_owner AND lease_token = :lease_token
                      AND lease_expires_at > clock_timestamp()
                    FOR UPDATE
                    """
                ),
                _claim_params(claim),
            )
        ).first()
        if row is None or row[2] != "running":
            return False
        if row[3] is not None:
            cancelled = await conn.execute(
                text(
                    """
                    UPDATE task_queue
                    SET status = 'cancelled', completed_at = now(),
                        last_error = COALESCE(cancel_reason, 'cancelled'),
                        lease_owner = NULL, lease_expires_at = NULL,
                        updated_at = now()
                    WHERE id = :task_id AND status = 'running'
                      AND lease_owner = :lease_owner AND lease_token = :lease_token
                      AND lease_expires_at > clock_timestamp()
                    """
                ),
                _claim_params(claim),
            )
            return bool(cancelled.rowcount)
        attempt_count = int(row[0] or 1)
        max_attempts = int(row[1] or 1)
        if attempt_count >= max_attempts:
            await _finish_task_failed(conn, claim, error)
            return True
        retry_at = _retry_at(attempt_count)
        retried = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'retrying',
                    available_at = CAST(:retry_at AS TIMESTAMPTZ),
                    deadline_at = CAST(:retry_at AS TIMESTAMPTZ)
                        + make_interval(secs => :deadline_seconds),
                    last_error = :error,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE id = :task_id AND status = 'running'
                  AND lease_owner = :lease_owner AND lease_token = :lease_token
                  AND lease_expires_at > clock_timestamp()
                """
            ),
            {
                **_claim_params(claim),
                "retry_at": retry_at,
                "deadline_seconds": _TRACKER_DEADLINE_SECONDS,
                "error": error[:500],
            },
        )
        return bool(retried.rowcount)


async def _finish_task_failed(
    conn: AsyncConnection,
    claim: TrackerTaskClaim,
    error: str,
) -> None:
    failed = await conn.execute(
        text(
            """
            UPDATE task_queue
            SET status = 'failed', last_error = :error,
                completed_at = now(), lease_owner = NULL, lease_expires_at = NULL,
                updated_at = now()
            WHERE id = :task_id AND status = 'running'
              AND lease_owner = :lease_owner AND lease_token = :lease_token
              AND lease_expires_at > clock_timestamp()
            """
        ),
        {**_claim_params(claim), "error": error[:500]},
    )
    if not failed.rowcount:
        raise TrackerLeaseLostError(f"tracker task {claim.task_id} lost fence during failure")


def _retry_at(attempt_count: int) -> datetime:
    delay = min(_RETRY_BASE_SECONDS * (2 ** max(attempt_count - 1, 0)), _RETRY_MAX_SECONDS)
    return datetime.now(UTC) + timedelta(seconds=delay)


__all__ = [
    "CancelResult",
    "ProcessResult",
    "TrackerLeaseLostError",
    "TrackerTaskClaim",
    "attribution_conflicts",
    "cancel_unstarted_auto_pause",
    "claim_event_tasks",
    "confirmed_deposit_at",
    "mark_task_retry",
    "process_event_task",
]

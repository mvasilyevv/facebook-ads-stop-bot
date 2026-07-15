# -*- coding: utf-8 -*-
"""Durable processing and click-state projection for AdSet.pro events."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.adset_pro.ingest import (
    AttributionResult,
    canonical_event_type,
    resolve_attribution,
)
from core.adset_pro.schemas import PostbackEvent

_TASK_TYPE = "tracker_event_process"
_RETRY_BASE_SECONDS = 30
_RETRY_MAX_SECONDS = 300

# N-1 (a3b7) stores provider values verbatim. Keep raw rows unchanged so a
# repeated application rollback still sees its own event vocabulary; current
# code canonicalizes only while reading/projecting them.
LEGACY_POSITIVE_EVENT_TYPES = (
    "registration",
    "reg",
    "signup",
    "hold",
    "cpa_hold",
    "ftd",
    "first_deposit",
    "first-deposit",
    "first deposit",
    "accept",
    "cpa_accept",
    "redeposit",
    "redep",
    "cpa_redep",
)


def _canonical_event_type_sql(column: str = "event_type") -> str:
    """Static SQL CASE matching ``canonical_event_type`` for transition rows."""
    normalized = f"replace(lower(trim({column})), ' ', '_')"
    return f"""CASE
        WHEN {normalized} IN ('registration', 'reg', 'signup', 'hold', 'cpa_hold')
            THEN 'registration'
        WHEN {normalized} IN (
            'ftd', 'first_deposit', 'first-deposit', 'accept', 'cpa_accept'
        ) THEN 'ftd'
        WHEN {normalized} IN ('redeposit', 'redep', 'cpa_redep')
            THEN 'redeposit'
        ELSE NULL
    END"""


_CANONICAL_EVENT_TYPE_SQL = _canonical_event_type_sql()


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
    auto_cancel_shadow_candidate: bool = False


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


async def claim_event_tasks(engine: AsyncEngine, *, limit: int = 100) -> list[int]:
    """Claim runnable tracker tasks using ``FOR UPDATE SKIP LOCKED``."""
    async with engine.begin() as conn:
        # N-1 rollback inserts directly into the inbox and does not create the
        # event-driven outbox task. Recover those rows without rewriting their
        # raw event_type (old code still needs redep/baddep on a repeated rollback).
        await conn.execute(
            text(
                """
                UPDATE adsetpro_postback_events
                SET provider_event_id = COALESCE(
                        provider_event_id,
                        NULLIF(raw_json->>'provider_event_id', ''),
                        NULLIF(raw_json->>'event_id', ''),
                        NULLIF(raw_json->>'transaction_id', ''),
                        NULLIF(raw_json->>'transactionId', ''),
                        NULLIF(raw_json->>'txn_id', ''),
                        NULLIF(raw_json->>'conversion_id', ''),
                        NULLIF(raw_json->>'postback_id', '')
                    )
                WHERE processed_at IS NULL
                  AND provider_event_id IS NULL
                """
            )
        )
        await conn.execute(
            text(
                f"""
                UPDATE adsetpro_postback_events
                SET processed_at = now(),
                    attribution_status = 'ignored',
                    next_retry_at = NULL,
                    last_error = 'ignored_legacy_event_type:' || left(event_type, 128)
                WHERE processed_at IS NULL
                  AND is_duplicate = FALSE
                  AND ({_CANONICAL_EVENT_TYPE_SQL}) IS NULL
                """
            )
        )
        await conn.execute(
            text(
                f"""
                INSERT INTO task_queue
                    (task_type, status, idempotency_key, payload, requested_by,
                     attempt_count, max_attempts, created_at, updated_at)
                SELECT
                    'tracker_event_process',
                    'pending',
                    left(
                        'tracker:recover:' || e.source || ':' || e.id::text || ':' ||
                        extract(epoch FROM e.received_at)::numeric::text,
                        128
                    ),
                    jsonb_build_object(
                        'event_id', e.id,
                        'received_at',
                            to_char(
                                e.received_at AT TIME ZONE 'UTC',
                                'YYYY-MM-DD"T"HH24:MI:SS.US'
                            ) || '+00:00',
                        'source', e.source,
                        'click_id', e.click_id
                    ),
                    'tracker_n1_recovery',
                    0,
                    10080,
                    now(),
                    now()
                FROM adsetpro_postback_events e
                WHERE e.processed_at IS NULL
                  AND e.is_duplicate = FALSE
                  AND ({_canonical_event_type_sql("e.event_type")}) IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM task_queue q
                      WHERE q.task_type = 'tracker_event_process'
                        AND q.payload->>'event_id' = e.id::text
                  )
                ON CONFLICT (idempotency_key) DO NOTHING
                """
            )
        )
        # Older code left exhausted retrying rows permanently non-runnable. Move
        # them to the terminal dead-letter state before claiming fresh work.
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'failed',
                    next_retry_at = NULL,
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
                          AND (next_retry_at IS NULL OR next_retry_at <= now())
                          AND attempt_count < max_attempts
                        ORDER BY created_at, id
                        FOR UPDATE SKIP LOCKED
                        LIMIT :limit
                    )
                    UPDATE task_queue q
                    SET status = 'running',
                        attempt_count = q.attempt_count + 1,
                        updated_at = now()
                    FROM candidates c
                    WHERE q.id = c.id
                    RETURNING q.id
                    """
                ),
                {"limit": limit},
            )
        ).all()
    return [int(row[0]) for row in rows]


async def process_event_task(
    engine: AsyncEngine,
    *,
    task_id: int,
    auto_cancel_enabled: bool = False,
) -> ProcessResult:
    """Project one claimed event and finish/retry its durable task atomically."""
    async with engine.begin() as conn:
        task = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT id, status, payload, attempt_count, max_attempts
                    FROM task_queue
                    WHERE id = :task_id AND task_type = 'tracker_event_process'
                    FOR UPDATE
                    """
                    ),
                    {"task_id": task_id},
                )
            )
            .mappings()
            .first()
        )
        if task is None:
            return ProcessResult(
                task_id=task_id,
                event_id=None,
                processed=False,
                attribution_status="missing_task",
            )
        if task["status"] != "running":
            return ProcessResult(
                task_id=task_id,
                event_id=None,
                processed=False,
                attribution_status=f"task_{task['status']}",
            )

        payload = (
            task["payload"] if isinstance(task["payload"], dict) else json.loads(task["payload"])
        )
        event_id = int(payload["event_id"])
        received_at = datetime.fromisoformat(str(payload["received_at"]).replace("Z", "+00:00"))
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
            await _finish_task_failed(conn, task_id, "tracker event is missing")
            return ProcessResult(
                task_id=task_id,
                event_id=event_id,
                processed=False,
                attribution_status="missing_event",
            )

        event_type = canonical_event_type(str(event["event_type"]))
        if event_type is None:
            await conn.execute(
                text(
                    """
                    UPDATE adsetpro_postback_events
                    SET processed_at = now(), attribution_status = 'ignored',
                        next_retry_at = NULL,
                        last_error = 'ignored_legacy_event_type:' || left(event_type, 128)
                    WHERE id = :event_id AND received_at = :received_at
                    """
                ),
                {"event_id": event_id, "received_at": received_at},
            )
            await _finish_task_ignored(conn, task_id, str(event["event_type"]))
            return ProcessResult(
                task_id=task_id,
                event_id=event_id,
                processed=False,
                attribution_status="ignored",
                occurred_at=event["occurred_at"],
                received_at=event["received_at"],
            )
        event = dict(event)
        event["event_type"] = event_type

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
                    f"""
                    SELECT DISTINCT ad_id, fb_ad_id
                    FROM (
                        SELECT fb_ad_fk AS ad_id, fb_ad_id
                        FROM adsetpro_postback_events
                        WHERE source = :source
                          AND click_id = :click_id
                          AND is_duplicate = FALSE
                          AND ({_canonical_event_type_sql("event_type")}) IS NOT NULL
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
            await _finish_task_failed(conn, task_id, error)
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
                    f"""
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
                      AND ({_canonical_event_type_sql("event_type")}) IS NOT NULL
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
        shadow_candidate = False
        if attribution.fb_ad_id and event["event_type"] in {"registration", "ftd"}:
            if auto_cancel_enabled:
                cancel = await cancel_unstarted_auto_pause(
                    conn,
                    fb_ad_id=attribution.fb_ad_id,
                    now=datetime.now(UTC),
                )
            else:
                shadow_candidate = await has_unstarted_auto_pause(
                    conn,
                    fb_ad_id=attribution.fb_ad_id,
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
                await _finish_task_failed(conn, task_id, error)
            else:
                await conn.execute(
                    text(
                        """
                        UPDATE task_queue
                        SET status = 'retrying', next_retry_at = :retry_at,
                            last_error = :error, updated_at = now()
                        WHERE id = :task_id AND status = 'running'
                        """
                    ),
                    {"retry_at": retry_at, "error": error, "task_id": task_id},
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
                auto_cancel_shadow_candidate=shadow_candidate,
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
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'succeeded', result = CAST(:result AS JSONB),
                    next_retry_at = NULL, last_error = NULL,
                    completed_at = now(), updated_at = now()
                WHERE id = :task_id AND status = 'running'
                """
            ),
            {
                "task_id": task_id,
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
            auto_cancel_shadow_candidate=shadow_candidate,
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
        revenue=Decimal(event["revenue"] or 0),
        currency=str(event["currency"] or "USD"),
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
                    f"""
                WITH normalized AS (
                    SELECT id, received_at, occurred_at, revenue, raw_json,
                           {_canonical_event_type_sql("event_type")} AS canonical_type
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
                        WHERE canonical_type = 'registration'
                    ) AS registration_at,
                    MIN(occurred_at) FILTER (WHERE canonical_type = 'ftd') AS ftd_at,
                    COALESCE(
                        (ARRAY_AGG(revenue ORDER BY occurred_at, received_at, id)
                            FILTER (WHERE canonical_type = 'ftd'))[1],
                        0
                    ) AS ftd_revenue,
                    COUNT(*) FILTER (WHERE canonical_type = 'redeposit')::int AS redeposits,
                    COALESCE(
                        SUM(revenue) FILTER (WHERE canonical_type = 'redeposit'), 0
                    )
                        AS redeposit_revenue,
                    MAX(received_at) AS last_event_at,
                    UPPER(COALESCE(
                        (ARRAY_AGG(raw_json->>'country' ORDER BY received_at DESC)
                            FILTER (WHERE raw_json->>'country' IS NOT NULL))[1],
                        (ARRAY_AGG(raw_json->>'country_code' ORDER BY received_at DESC)
                            FILTER (WHERE raw_json->>'country_code' IS NOT NULL))[1],
                        (ARRAY_AGG(raw_json->>'geo' ORDER BY received_at DESC)
                            FILTER (WHERE raw_json->>'geo' IS NOT NULL))[1]
                    )) AS country
                FROM normalized
                WHERE canonical_type IS NOT NULL
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
                     confirmed_deposit_at, ftd_revenue, redeposits, redeposit_revenue,
                     last_event_at, version, created_at, updated_at)
                VALUES
                    (gen_random_uuid(), :source, :click_id, :ad_id, :fb_ad_id, :country,
                     :attribution_status, :registration, :ftd, :confirmed,
                     :registration_at, :ftd_at, :confirmed_at, :ftd_revenue,
                     :redeposits, :redeposit_revenue, :last_event_at, 1, now(), now())
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
                    ftd_revenue = EXCLUDED.ftd_revenue,
                    redeposits = EXCLUDED.redeposits,
                    redeposit_revenue = EXCLUDED.redeposit_revenue,
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
                    "ftd_revenue": aggregate["ftd_revenue"],
                    "redeposits": aggregate["redeposits"],
                    "redeposit_revenue": aggregate["redeposit_revenue"],
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
                    completed_at = now(), updated_at = now()
                WHERE task_type = 'meta_api_mutation'
                  AND payload->>'mutation_kind' = 'pause_ad'
                  AND payload->>'target_id' = :fb_ad_id
                  AND requested_by = 'bot_auto_stop'
                  AND status IN ('pending', 'retrying', 'running')
                  AND external_started_at IS NULL
                RETURNING id
                """
            ),
            {"fb_ad_id": fb_ad_id},
        )
    ).all()
    return CancelResult(
        cancelled_task_ids=tuple(int(row[0]) for row in rows),
        meta_snapshot_fresh=is_fresh,
        needs_scan_refresh=not is_fresh,
    )


async def has_unstarted_auto_pause(
    conn: AsyncConnection,
    *,
    fb_ad_id: str,
) -> bool:
    """Read-only shadow decision under the same per-ad race lock."""
    await conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": fb_ad_id},
    )
    return bool(
        await conn.scalar(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM task_queue
                    WHERE task_type = 'meta_api_mutation'
                      AND payload->>'mutation_kind' = 'pause_ad'
                      AND payload->>'target_id' = :fb_ad_id
                      AND requested_by = 'bot_auto_stop'
                      AND status IN ('pending', 'retrying', 'running')
                      AND external_started_at IS NULL
                )
                """
            ),
            {"fb_ad_id": fb_ad_id},
        )
    )


async def mark_task_retry(engine: AsyncEngine, *, task_id: int, error: str) -> None:
    """Best-effort infra failure transition for a claimed tracker task."""
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT attempt_count, max_attempts, status
                    FROM task_queue
                    WHERE id = :id AND task_type = 'tracker_event_process'
                    FOR UPDATE
                    """
                ),
                {"id": task_id},
            )
        ).first()
        if row is None or row[2] != "running":
            return
        attempt_count = int(row[0] or 1)
        max_attempts = int(row[1] or 1)
        if attempt_count >= max_attempts:
            await _finish_task_failed(conn, task_id, error)
            return
        retry_at = _retry_at(attempt_count)
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'retrying', next_retry_at = :retry_at,
                    last_error = :error, updated_at = now()
                WHERE id = :id AND status = 'running'
                """
            ),
            {"retry_at": retry_at, "error": error[:500], "id": task_id},
        )


async def requeue_aggregation_repair(
    engine: AsyncEngine,
    *,
    task_id: int,
    error: str,
) -> None:
    """Durably retry a targeted aggregation that failed after projection commit."""
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT attempt_count, max_attempts, status
                    FROM task_queue
                    WHERE id = :id AND task_type = 'tracker_event_process'
                    FOR UPDATE
                    """
                ),
                {"id": task_id},
            )
        ).first()
        if row is None or row[2] != "succeeded":
            return
        attempt_count = int(row[0] or 1)
        max_attempts = int(row[1] or 1)
        if attempt_count >= max_attempts:
            await conn.execute(
                text(
                    """
                    UPDATE task_queue
                    SET status = 'failed', next_retry_at = NULL,
                        last_error = :error, completed_at = now(), updated_at = now()
                    WHERE id = :id AND status = 'succeeded'
                    """
                ),
                {"error": f"aggregation: {error}"[:500], "id": task_id},
            )
            return
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'retrying', next_retry_at = :retry_at,
                    last_error = :error, completed_at = NULL, updated_at = now()
                WHERE id = :id AND status = 'succeeded'
                """
            ),
            {
                "retry_at": _retry_at(attempt_count),
                "error": f"aggregation: {error}"[:500],
                "id": task_id,
            },
        )


async def _finish_task_failed(conn: AsyncConnection, task_id: int, error: str) -> None:
    await conn.execute(
        text(
            """
            UPDATE task_queue
            SET status = 'failed', next_retry_at = NULL, last_error = :error,
                completed_at = now(), updated_at = now()
            WHERE id = :id AND status = 'running'
            """
        ),
        {"error": error[:500], "id": task_id},
    )


async def _finish_task_ignored(
    conn: AsyncConnection,
    task_id: int,
    raw_event_type: str,
) -> None:
    """Close a legacy negative/malformed event without retry or dead-letter noise."""
    await conn.execute(
        text(
            """
            UPDATE task_queue
            SET status = 'succeeded', next_retry_at = NULL, last_error = NULL,
                result = jsonb_build_object(
                    'status', 'ignored', 'raw_event_type', :raw_event_type
                ),
                completed_at = now(), updated_at = now()
            WHERE id = :id AND status = 'running'
            """
        ),
        {"raw_event_type": raw_event_type[:128], "id": task_id},
    )


def _retry_at(attempt_count: int) -> datetime:
    delay = min(_RETRY_BASE_SECONDS * (2 ** max(attempt_count - 1, 0)), _RETRY_MAX_SECONDS)
    return datetime.now(UTC) + timedelta(seconds=delay)


__all__ = [
    "CancelResult",
    "ProcessResult",
    "attribution_conflicts",
    "cancel_unstarted_auto_pause",
    "claim_event_tasks",
    "confirmed_deposit_at",
    "has_unstarted_auto_pause",
    "mark_task_retry",
    "process_event_task",
    "requeue_aggregation_repair",
]

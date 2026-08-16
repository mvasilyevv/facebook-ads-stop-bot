# -*- coding: utf-8 -*-
"""Durable PostgreSQL control plane for explicit observer scans.

The queue row is the only command authority.  Redis is deliberately absent:
API and scheduler callers enqueue one ``observer_scan`` task, while the
observer claims it with the canonical lease/fencing helpers and polls the
database for cancellation and deadline changes during execution.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.observer.queries import load_scanning_enabled
from core.tasks.queue import Task, claim_next_task, create_task

OBSERVER_SCAN_TASK_TYPE = "observer_scan"
OBSERVER_SCAN_DEADLINE_SECONDS = 120
OBSERVER_SCAN_LEASE_SECONDS = 150
OBSERVER_SCAN_POLL_SECONDS = 0.5
_OBSERVER_SCAN_BARRIER_PARK_DAYS = 36500

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class ObserverScanReceipt:
    task_id: int
    created: bool
    correlation_id: uuid.UUID


class ObserverScanControlStop(BaseException):
    """Non-swallowable control-plane stop for a claimed scan."""


class ObserverScanFenceLost(ObserverScanControlStop):
    """The observer no longer owns the claimed task lease."""


class ObserverScanCancelled(ObserverScanControlStop):
    """Cancellation or the absolute deadline stopped the scan."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"observer scan stopped: {reason}")


async def lock_observer_scan_publication(conn: AsyncConnection) -> None:
    """Serialize scheduler and operator publishers before their active-task check."""
    await conn.execute(
        text(
            """
            SELECT pg_advisory_xact_lock(
              hashtext('fb-agent'),
              hashtext('observer-scan-publication')
            )
            """
        )
    )


def observer_scan_idempotency_key(namespace: str, value: str) -> str:
    """Build a bounded opaque key without leaking raw operator input."""
    scope = namespace.strip().lower().replace("_", "-")[:32]
    if not scope:
        raise ValueError("observer scan namespace must not be empty")
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
    return f"observer-scan:{scope}:{digest}"


async def enqueue_observer_scan(
    engine: AsyncEngine,
    *,
    requested_by: str,
    reason: str,
    idempotency_key: str,
    available_at: datetime | None = None,
    connection: AsyncConnection | None = None,
    dependency_task_ids: Sequence[int] | None = None,
    lane: str = "interactive",
    priority: int = 75,
) -> ObserverScanReceipt:
    """Create or resolve one durable scan task."""
    requested_by_value = requested_by.strip()[:64]
    reason_value = reason.strip()[:256]
    if not requested_by_value:
        raise ValueError("requested_by must not be empty")
    if not reason_value:
        raise ValueError("reason must not be empty")
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValueError("idempotency_key must contain 1..128 characters")
    if lane not in {"interactive", "background"}:
        raise ValueError("observer scan lane must be interactive or background")

    dependencies = tuple(sorted({int(task_id) for task_id in dependency_task_ids or ()}))
    if any(task_id <= 0 for task_id in dependencies):
        raise ValueError("observer scan dependency task ids must be positive")
    if dependencies and available_at is not None:
        raise ValueError("dependency-gated observer scans cannot set available_at")

    now = datetime.now(UTC)
    effective_available_at = (
        now + timedelta(days=_OBSERVER_SCAN_BARRIER_PARK_DAYS)
        if dependencies
        else available_at or now
    )
    deadline_at = max(now, effective_available_at) + timedelta(
        seconds=OBSERVER_SCAN_DEADLINE_SECONDS
    )
    payload: dict[str, object] = {"reason": reason_value}
    if dependencies:
        payload.update(
            {
                "dependency_task_ids": list(dependencies),
                "dependency_state": "waiting",
            }
        )
    correlation_id = uuid.uuid4()
    task_id = await create_task(
        engine,
        task_type=OBSERVER_SCAN_TASK_TYPE,
        idempotency_key=idempotency_key,
        payload=payload,
        requested_by=requested_by_value,
        status="pending",
        max_attempts=1,
        lane=lane,
        priority=int(priority),
        available_at=effective_available_at,
        deadline_at=deadline_at,
        correlation_id=correlation_id,
        connection=connection,
    )
    if task_id is not None:
        return ObserverScanReceipt(
            task_id=task_id,
            created=True,
            correlation_id=correlation_id,
        )

    async def _load_existing(conn: AsyncConnection) -> ObserverScanReceipt:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT id, task_type, correlation_id, payload, lane, priority
                    FROM task_queue
                    WHERE idempotency_key = :idempotency_key
                    LIMIT 1
                    """
                ),
                {"idempotency_key": idempotency_key},
            )
        ).first()
        if row is None:
            raise RuntimeError("observer scan idempotency row disappeared")
        if str(row.task_type) != OBSERVER_SCAN_TASK_TYPE:
            raise RuntimeError("observer scan idempotency key is bound to another task type")
        stored_payload = row.payload if isinstance(row.payload, dict) else {}
        if stored_payload.get("reason") != reason_value:
            raise RuntimeError("observer scan idempotency key is bound to another reason")
        if str(row.lane) != lane or int(row.priority) != int(priority):
            raise RuntimeError("observer scan idempotency key is bound to another scheduling class")
        raw_stored_dependencies = stored_payload.get("dependency_task_ids", [])
        if not isinstance(raw_stored_dependencies, list):
            raise RuntimeError("observer scan dependency payload is malformed")
        stored_dependencies = tuple(sorted(int(task_id) for task_id in raw_stored_dependencies))
        if stored_dependencies != dependencies:
            raise RuntimeError("observer scan idempotency key is bound to other dependencies")
        return ObserverScanReceipt(
            task_id=int(row.id),
            created=False,
            correlation_id=uuid.UUID(str(row.correlation_id)),
        )

    if connection is not None:
        return await _load_existing(connection)
    async with engine.connect() as conn:
        return await _load_existing(conn)


async def enqueue_scheduled_observer_scan(
    engine: AsyncEngine,
    *,
    now: datetime | None = None,
) -> ObserverScanReceipt | None:
    """Publish exactly one outstanding adaptive background scan.

    The observer may legitimately run more than once inside the 120-second
    operation deadline, so a wall-clock bucket is not an execution identity.
    A shared PostgreSQL advisory transaction lock serializes scheduler and
    operator publishers. An already runnable pending/running scan is reused;
    after it reaches a terminal state the next adaptive tick receives a fresh
    durable task.

    Returns ``None`` while the owner keeps scanning switched off: on pause there
    is no work to publish at all.
    """

    # Публиковать нечего: на паузе скан немедленно вернёт outcome=paused, и
    # каждая такая задача осядет в ленте оператора как отказ. Точка чтения
    # та же, что у остальных воркеров, замирающих на глобальном стопе.
    if not await load_scanning_enabled(engine):
        return None

    scheduled_at = now or datetime.now(UTC)
    async with engine.begin() as conn:
        await lock_observer_scan_publication(conn)
        existing_rows = (
            await conn.execute(
                text(
                    """
                    SELECT id, correlation_id, lane, priority, status, deadline_at,
                           requested_by, payload
                    FROM task_queue
                    WHERE task_type = 'observer_scan'
                      AND status IN ('pending', 'retrying', 'running')
                      AND cancel_requested_at IS NULL
                      AND COALESCE(payload->>'dependency_state', '') <> 'waiting'
                    ORDER BY
                      CASE status WHEN 'running' THEN 0 ELSE 1 END,
                      priority DESC,
                      created_at,
                      id
                    LIMIT 2
                    FOR UPDATE
                    """
                )
            )
        ).all()
        if len(existing_rows) > 1:
            raise RuntimeError("multiple runnable observer scans violate the singleton contract")
        if existing_rows:
            existing = existing_rows[0]
            payload = existing.payload if isinstance(existing.payload, dict) else {}
            is_scheduled = (
                str(existing.requested_by) == "observer_scheduler"
                and payload.get("reason") == "adaptive_schedule"
            )
            scheduling_class = (str(existing.lane), int(existing.priority))
            scheduled_class_valid = scheduling_class == ("background", 10) or (
                scheduling_class[0] == "interactive" and scheduling_class[1] >= 75
            )
            if is_scheduled and not scheduled_class_valid:
                raise RuntimeError(
                    "outstanding scheduled observer scan has an invalid scheduling class"
                )
            if is_scheduled and str(existing.status) in {"pending", "retrying"}:
                await conn.execute(
                    text(
                        """
                        UPDATE task_queue
                        SET deadline_at = GREATEST(
                              COALESCE(
                                deadline_at,
                                '-infinity'::timestamptz
                              ),
                              available_at
                                + make_interval(secs => :deadline_seconds),
                              clock_timestamp()
                                + make_interval(secs => :deadline_seconds)
                            ),
                            updated_at = clock_timestamp()
                        WHERE id = :task_id
                          AND status IN ('pending', 'retrying')
                          AND cancel_requested_at IS NULL
                        """
                    ),
                    {
                        "task_id": int(existing.id),
                        "deadline_seconds": OBSERVER_SCAN_DEADLINE_SECONDS,
                    },
                )
            return ObserverScanReceipt(
                task_id=int(existing.id),
                created=False,
                correlation_id=uuid.UUID(str(existing.correlation_id)),
            )

        return await enqueue_observer_scan(
            engine,
            requested_by="observer_scheduler",
            reason="adaptive_schedule",
            idempotency_key=observer_scan_idempotency_key(
                "scheduler",
                f"{scheduled_at.isoformat()}:{uuid.uuid4().hex}",
            ),
            lane="background",
            priority=10,
            connection=conn,
        )


async def claim_observer_scan(
    engine: AsyncEngine,
    *,
    worker_id: uuid.UUID,
) -> Task | None:
    """Release completed dependency barriers, then claim one runnable scan."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE task_queue AS scan
                SET available_at = clock_timestamp(),
                    deadline_at = clock_timestamp()
                        + make_interval(secs => :deadline_seconds),
                    payload = scan.payload || jsonb_build_object(
                        'dependency_state', 'ready',
                        'dependencies_released_at', clock_timestamp()
                    ),
                    updated_at = clock_timestamp()
                WHERE scan.task_type = 'observer_scan'
                  AND scan.status IN ('pending', 'retrying')
                  AND scan.payload @> '{"dependency_state":"waiting"}'::jsonb
                  AND jsonb_typeof(scan.payload->'dependency_task_ids') = 'array'
                  AND jsonb_array_length(scan.payload->'dependency_task_ids') > 0
                  AND NOT EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements_text(
                          scan.payload->'dependency_task_ids'
                      ) AS dependency(task_id)
                      LEFT JOIN task_queue AS child
                        ON child.id = CAST(dependency.task_id AS BIGINT)
                      WHERE child.id IS NULL
                         OR child.status NOT IN ('succeeded', 'failed', 'cancelled')
                  )
                """
            ),
            {"deadline_seconds": OBSERVER_SCAN_DEADLINE_SECONDS},
        )
    claim = await claim_next_task(
        engine,
        task_type=OBSERVER_SCAN_TASK_TYPE,
        lanes=("interactive", "background"),
        worker_id=worker_id,
        lease_seconds=OBSERVER_SCAN_LEASE_SECONDS,
    )
    return claim.task


async def _assert_scan_control(
    engine: AsyncEngine,
    task: Task,
) -> None:
    if task.lease_owner is None or int(task.lease_token or 0) <= 0:
        raise ValueError("observer scan task requires a valid lease fence")
    params = {
        "task_id": int(task.id),
        "lease_owner": task.lease_owner,
        "lease_token": int(task.lease_token),
    }
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT cancel_requested_at, deadline_at
                    FROM task_queue
                    WHERE id = :task_id AND status = 'running'
                      AND lease_owner = :lease_owner
                      AND lease_token = :lease_token
                      AND lease_expires_at > clock_timestamp()
                    """
                ),
                params,
            )
        ).first()
    if row is None:
        raise ObserverScanFenceLost(f"observer scan task {task.id} lost its lease")
    if row.cancel_requested_at is not None:
        raise ObserverScanCancelled("cancel_requested")
    deadline_at = row.deadline_at
    if deadline_at is not None:
        aware_deadline = deadline_at if deadline_at.tzinfo else deadline_at.replace(tzinfo=UTC)
        if aware_deadline <= datetime.now(UTC):
            raise ObserverScanCancelled("deadline_exceeded")


async def _wait_for_control_stop(
    engine: AsyncEngine,
    task: Task,
    *,
    poll_interval_seconds: float,
) -> None:
    while True:
        await _assert_scan_control(engine, task)
        await asyncio.sleep(max(0.05, poll_interval_seconds))


async def run_with_observer_scan_control(
    engine: AsyncEngine,
    task: Task,
    operation_factory: Callable[[], Awaitable[_T]],
    *,
    poll_interval_seconds: float = 0.25,
) -> _T:
    """Cancel the active scan when DB cancellation, deadline or fencing wins."""
    # Do not let the read operation start under an already-stale fence.  The
    # background monitor then closes the race for changes after this check.
    await _assert_scan_control(engine, task)
    operation_task = asyncio.create_task(operation_factory())
    control_task = asyncio.create_task(
        _wait_for_control_stop(
            engine,
            task,
            poll_interval_seconds=poll_interval_seconds,
        )
    )
    try:
        done, _ = await asyncio.wait(
            {operation_task, control_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if control_task in done:
            operation_task.cancel()
            try:
                await operation_task
            except asyncio.CancelledError:
                pass
            control_task.result()
            raise RuntimeError("observer scan control monitor stopped without a reason")
        return operation_task.result()
    finally:
        for background_task in (operation_task, control_task):
            if not background_task.done():
                background_task.cancel()
        for background_task in (operation_task, control_task):
            try:
                await background_task
            except asyncio.CancelledError:
                pass
            except ObserverScanControlStop:
                pass
            except Exception:
                pass


__all__ = [
    "OBSERVER_SCAN_DEADLINE_SECONDS",
    "OBSERVER_SCAN_LEASE_SECONDS",
    "OBSERVER_SCAN_POLL_SECONDS",
    "OBSERVER_SCAN_TASK_TYPE",
    "ObserverScanCancelled",
    "ObserverScanControlStop",
    "ObserverScanFenceLost",
    "ObserverScanReceipt",
    "claim_observer_scan",
    "enqueue_observer_scan",
    "observer_scan_idempotency_key",
    "run_with_observer_scan_control",
]

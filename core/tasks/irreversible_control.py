"""Safety controls for irreversible campaign-creation operations.

The task row is the authority for cancellation, deadlines and lease ownership.
An external boundary is persisted under the current lease immediately before an
irreversible RPC.  Once that boundary exists, callers must treat any interrupted
operation as ``UNKNOWN`` and must never retry it blindly.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.tasks.queue import Task

_T = TypeVar("_T")


class CreatorTaskStop(BaseException):
    """Non-swallowable control signal for an irreversible creator operation."""


class CreatorTaskFenceLost(CreatorTaskStop):
    """The worker no longer owns the task and must stop all side effects."""


class CreatorTaskControlAbort(CreatorTaskStop):
    """A DB-authoritative cancel/deadline stopped creator execution."""

    def __init__(self, reason: str, *, external_started: bool) -> None:
        self.reason = reason
        self.external_started = external_started
        super().__init__(f"creator task interrupted: {reason}; external_started={external_started}")


@dataclass
class CreatorTaskControl:
    """Fenced control-plane view for one claimed creator task."""

    engine: AsyncEngine
    task: Task
    operation: str
    target_id: str
    external_started: bool = field(init=False)

    def __post_init__(self) -> None:
        if self.task.lease_owner is None or int(self.task.lease_token or 0) <= 0:
            raise ValueError("creator task requires a valid lease fence")
        self.external_started = self.task.external_started_at is not None

    @property
    def _params(self) -> dict[str, object]:
        return {
            "task_id": int(self.task.id),
            "lease_owner": self.task.lease_owner,
            "lease_token": int(self.task.lease_token),
        }

    @staticmethod
    def _control_reason(row: object) -> str | None:
        if row.cancel_requested_at is not None:
            return "cancel_requested"
        deadline_at = row.deadline_at
        if deadline_at is not None:
            aware = deadline_at if deadline_at.tzinfo else deadline_at.replace(tzinfo=UTC)
            if aware <= datetime.now(UTC):
                return "deadline_exceeded"
        return None

    async def check(self) -> None:
        """Fail closed when cancel/deadline/fence changed since claim."""
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT external_started_at, cancel_requested_at, deadline_at
                        FROM task_queue
                        WHERE id = :task_id AND status = 'running'
                          AND lease_owner = :lease_owner AND lease_token = :lease_token
                          AND lease_expires_at > clock_timestamp()
                        """
                    ),
                    self._params,
                )
            ).first()
        if row is None:
            raise CreatorTaskFenceLost(
                f"creator task {self.task.id} lost lease before {self.operation}"
            )
        self.external_started = row.external_started_at is not None
        reason = self._control_reason(row)
        if reason is not None:
            raise CreatorTaskControlAbort(reason, external_started=self.external_started)

    async def begin_external(self, external_operation: str) -> None:
        """Atomically persist the irreversible boundary under the current fence.

        Cancellation and the absolute deadline are checked while the task row is
        locked.  Therefore a concurrent cancel either wins before the boundary
        (and no RPC is issued) or observes ``external_started_at`` afterwards.
        """
        if not external_operation:
            raise ValueError("external_operation is required")

        row = None
        async with self.engine.begin() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT external_started_at, cancel_requested_at, deadline_at
                        FROM task_queue
                        WHERE id = :task_id AND status = 'running'
                          AND lease_owner = :lease_owner AND lease_token = :lease_token
                          AND lease_expires_at > clock_timestamp()
                        FOR UPDATE
                        """
                    ),
                    self._params,
                )
            ).first()
            if row is not None:
                self.external_started = row.external_started_at is not None
                reason = self._control_reason(row)
                if reason is None:
                    result = await conn.execute(
                        text(
                            """
                            UPDATE task_queue
                            SET external_started_at = COALESCE(
                                    external_started_at, clock_timestamp()
                                ),
                                result = COALESCE(result, '{}'::jsonb)
                                    || jsonb_build_object(
                                        'external_boundary_operation',
                                        COALESCE(
                                            result->>'external_boundary_operation',
                                            CAST(:external_operation AS TEXT)
                                        ),
                                        'external_boundary_target',
                                        COALESCE(
                                            result->>'external_boundary_target',
                                            CAST(:target_id AS TEXT)
                                        )
                                    ),
                                updated_at = clock_timestamp()
                            WHERE id = :task_id AND status = 'running'
                              AND lease_owner = :lease_owner AND lease_token = :lease_token
                              AND lease_expires_at > clock_timestamp()
                              AND cancel_requested_at IS NULL
                              AND (
                                  deadline_at IS NULL
                                  OR deadline_at > clock_timestamp()
                              )
                            """
                        ),
                        {
                            **self._params,
                            "external_operation": external_operation[:128],
                            "target_id": self.target_id[:256],
                        },
                    )
                    if (result.rowcount or 0) <= 0:
                        # A deadline can cross while the row is locked between
                        # the SELECT and UPDATE. Re-check it before classifying
                        # the failed write as a stale fence. In either case the
                        # caller never reaches the external RPC.
                        if self._control_reason(row) is None:
                            row = None
                    else:
                        # Set this before transaction commit returns. If the
                        # commit acknowledgement is lost/cancelled, treating
                        # the outcome as post-boundary UNKNOWN is conservative
                        # and prevents a blind retry of a persisted boundary.
                        self.external_started = True

        if row is None:
            raise CreatorTaskFenceLost(
                f"creator task {self.task.id} lost lease at external boundary"
            )
        reason = self._control_reason(row)
        if reason is not None:
            raise CreatorTaskControlAbort(reason, external_started=self.external_started)
        self.external_started = True

    async def wait_for_abort(self, *, poll_interval_seconds: float = 0.5) -> None:
        """Poll DB control state until it requires the active RPC to stop."""
        while True:
            await asyncio.sleep(max(0.05, poll_interval_seconds))
            await self.check()


async def run_with_task_control(
    control: CreatorTaskControl,
    operation_factory: Callable[[], Awaitable[_T]],
    *,
    poll_interval_seconds: float = 0.5,
) -> _T:
    """Cancel ``operation`` when DB cancel/deadline/fencing wins the race."""
    await control.check()
    execution_task = asyncio.create_task(operation_factory())
    control_task = asyncio.create_task(
        control.wait_for_abort(poll_interval_seconds=poll_interval_seconds)
    )
    try:
        done, _ = await asyncio.wait(
            {execution_task, control_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        # Prefer the control plane if both tasks completed in the same loop turn.
        if control_task in done:
            execution_task.cancel()
            try:
                await execution_task
            except asyncio.CancelledError:
                pass
            control_task.result()
            raise RuntimeError("creator control monitor stopped without a reason")
        return execution_task.result()
    finally:
        for background_task in (execution_task, control_task):
            if not background_task.done():
                background_task.cancel()
        for background_task in (execution_task, control_task):
            try:
                await background_task
            except asyncio.CancelledError:
                pass
            except (CreatorTaskControlAbort, CreatorTaskFenceLost):
                # The primary branch above propagates the authoritative reason.
                pass
            except Exception:
                # Awaiting a completed execution task repeats its primary error;
                # it has already been returned/raised by the branch above.
                pass


def seconds_until_deadline(deadline_at: datetime | None) -> float | None:
    """Return a non-negative timeout suitable for asyncio and gRPC."""
    if deadline_at is None:
        return None
    aware = deadline_at if deadline_at.tzinfo else deadline_at.replace(tzinfo=UTC)
    return max(0.0, (aware - datetime.now(UTC)).total_seconds())


__all__ = [
    "CreatorTaskControl",
    "CreatorTaskControlAbort",
    "CreatorTaskFenceLost",
    "CreatorTaskStop",
    "run_with_task_control",
    "seconds_until_deadline",
]

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from core.tasks.irreversible_control import (
    CreatorTaskControl,
    CreatorTaskControlAbort,
    CreatorTaskFenceLost,
    run_with_task_control,
)
from core.tasks.queue import Task


class _Result:
    def __init__(self, *, row=None, rowcount: int = 0) -> None:
        self._row = row
        self.rowcount = rowcount

    def first(self):
        return self._row


class _Connection:
    def __init__(self, results: list[_Result]) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, statement, params):
        self.calls.append((str(statement), dict(params)))
        return self._results.pop(0)


class _Context:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return False


class _Engine:
    def __init__(self, results: list[_Result]) -> None:
        self.connection = _Connection(results)

    def begin(self):
        return _Context(self.connection)

    def connect(self):
        return _Context(self.connection)


def _task() -> Task:
    now = datetime.now(UTC)
    return Task(
        id=71,
        task_type="campaign_create",
        status="running",
        idempotency_key="creator-71",
        payload={"run_id": "run-71"},
        attempt_count=0,
        max_attempts=5,
        requested_by="test",
        last_error=None,
        created_at=now,
        external_started_at=None,
        result=None,
        lane="bulk",
        priority=0,
        available_at=now,
        deadline_at=now + timedelta(minutes=5),
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000071"),
        lease_token=13,
        lease_expires_at=now + timedelta(minutes=5),
        cancel_requested_at=None,
        cancel_reason=None,
        correlation_id=uuid.uuid4(),
    )


def _row(*, external=False, cancelled=False, expired=False):
    return SimpleNamespace(
        external_started_at=datetime.now(UTC) if external else None,
        cancel_requested_at=datetime.now(UTC) if cancelled else None,
        deadline_at=(
            datetime.now(UTC) - timedelta(seconds=1)
            if expired
            else datetime.now(UTC) + timedelta(minutes=5)
        ),
    )


def test_creator_stop_signals_are_not_swallowed_by_best_effort_handlers() -> None:
    assert not issubclass(CreatorTaskControlAbort, Exception)
    assert not issubclass(CreatorTaskFenceLost, Exception)


@pytest.mark.asyncio
async def test_external_boundary_is_fenced_and_committed_before_rpc() -> None:
    engine = _Engine([_Result(row=_row()), _Result(rowcount=1)])
    control = CreatorTaskControl(engine, _task(), "campaign_create", "run-71")

    await control.begin_external("CampaignCreator.Create")

    assert control.external_started is True
    select_sql, select_params = engine.connection.calls[0]
    update_sql, update_params = engine.connection.calls[1]
    assert "FOR UPDATE" in select_sql
    assert "lease_owner = :lease_owner" in select_sql
    assert "lease_token = :lease_token" in select_sql
    assert "lease_expires_at > clock_timestamp()" in select_sql
    assert (
        "external_started_at = COALESCE(\n                                    external_started_at, clock_timestamp()"
        in update_sql
    )
    assert "cancel_requested_at IS NULL" in update_sql
    assert "deadline_at > clock_timestamp()" in update_sql
    assert update_params["lease_owner"] == _task().lease_owner
    assert update_params["lease_token"] == 13


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "reason"),
    [
        (_row(cancelled=True), "cancel_requested"),
        (_row(expired=True), "deadline_exceeded"),
    ],
)
async def test_pre_boundary_cancel_or_deadline_blocks_external_call(row, reason) -> None:
    engine = _Engine([_Result(row=row)])
    control = CreatorTaskControl(engine, _task(), "campaign_create", "run-71")

    with pytest.raises(CreatorTaskControlAbort) as exc_info:
        await control.begin_external("CampaignCreator.Create")

    assert exc_info.value.reason == reason
    assert exc_info.value.external_started is False
    assert len(engine.connection.calls) == 1


@pytest.mark.asyncio
async def test_stale_fence_blocks_boundary_without_update() -> None:
    engine = _Engine([_Result(row=None)])
    control = CreatorTaskControl(engine, _task(), "campaign_create", "run-71")

    with pytest.raises(CreatorTaskFenceLost):
        await control.begin_external("CampaignCreator.Create")

    assert len(engine.connection.calls) == 1


@pytest.mark.asyncio
async def test_cancel_after_boundary_is_reported_as_unknown_capable() -> None:
    engine = _Engine([_Result(row=_row(external=True, cancelled=True))])
    control = CreatorTaskControl(engine, _task(), "campaign_create", "run-71")

    with pytest.raises(CreatorTaskControlAbort) as exc_info:
        await control.check()

    assert exc_info.value.reason == "cancel_requested"
    assert exc_info.value.external_started is True
    assert control.external_started is True


@pytest.mark.asyncio
async def test_runtime_control_cancels_active_rpc_coroutine() -> None:
    operation_cancelled = asyncio.Event()

    class _RuntimeControl:
        async def check(self) -> None:
            return None

        async def wait_for_abort(self, *, poll_interval_seconds: float) -> None:
            await asyncio.sleep(0)
            raise CreatorTaskControlAbort("cancel_requested", external_started=True)

    async def _operation() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            operation_cancelled.set()

    with pytest.raises(CreatorTaskControlAbort):
        await run_with_task_control(_RuntimeControl(), _operation)

    assert operation_cancelled.is_set()

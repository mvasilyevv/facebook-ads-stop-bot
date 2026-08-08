"""Projection ordering, retry and cancellation race contracts."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.adset_pro.processing import (
    TrackerTaskClaim,
    _retry_at,
    attribution_conflicts,
    cancel_unstarted_auto_pause,
    claim_event_tasks,
    confirmed_deposit_at,
    mark_task_retry,
)


@pytest.mark.parametrize("registration_first", [True, False])
def test_confirmed_deposit_requires_pair_regardless_of_event_order(
    registration_first: bool,
) -> None:
    first = datetime(2026, 7, 14, 10, 0, tzinfo=UTC)
    second = first + timedelta(seconds=2)
    registration_at, ftd_at = (first, second) if registration_first else (second, first)
    assert confirmed_deposit_at(registration_at, ftd_at) == second
    assert confirmed_deposit_at(registration_at, None) is None
    assert confirmed_deposit_at(None, ftd_at) is None


def test_unmatched_retry_backoff_is_bounded() -> None:
    now = datetime.now(UTC)
    first = _retry_at(1)
    later = _retry_at(20)
    assert timedelta(seconds=25) <= first - now <= timedelta(seconds=35)
    assert timedelta(seconds=295) <= later - now <= timedelta(seconds=305)


def test_conflicting_ad_attribution_is_detected_without_false_positive() -> None:
    assert attribution_conflicts("ad-b", ["ad-a", "ad-b"]) is True
    assert attribution_conflicts("ad-b", ["ad-a"]) is True
    assert attribution_conflicts("ad-a", ["ad-a"]) is False
    assert attribution_conflicts(None, ["ad-a"]) is False


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]], *, rowcount: int | None = None):
        self.rows = rows
        self.rowcount = len(rows) if rowcount is None else rowcount

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


class _Conn:
    def __init__(self, results: list[_Result]):
        self.results = list(results)
        self.executed: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement, params=None):
        self.executed.append((str(statement), params or {}))
        return self.results.pop(0)


class _Engine:
    def __init__(self, results: list[_Result]):
        self.conn = _Conn(results)

    @asynccontextmanager
    async def begin(self):
        yield self.conn


def _claim(task_id: int = 91) -> TrackerTaskClaim:
    now = datetime.now(UTC)
    return TrackerTaskClaim(
        task_id=task_id,
        lease_owner=uuid.UUID("11111111-1111-4111-8111-111111111111"),
        lease_token=3,
        lease_expires_at=now + timedelta(minutes=2),
        deadline_at=now + timedelta(minutes=2),
    )


@pytest.mark.asyncio
async def test_claim_terminalizes_exhausted_tasks_before_claiming() -> None:
    claim = _claim()
    engine = _Engine(
        [
            _Result([]),  # terminalize exhausted tasks
            _Result(
                [
                    (
                        claim.task_id,
                        claim.lease_owner,
                        claim.lease_token,
                        claim.lease_expires_at,
                        claim.deadline_at,
                    )
                ]
            ),  # claim runnable task
        ]
    )

    claimed = await claim_event_tasks(engine, limit=10, worker_id=claim.lease_owner)

    assert claimed == [claim]
    terminal_sql = engine.conn.executed[0][0]
    assert "status = 'failed'" in terminal_sql
    assert "attempt_count >= max_attempts" in terminal_sql


@pytest.mark.asyncio
async def test_infra_failure_on_last_attempt_dead_letters_task() -> None:
    claim = _claim()
    engine = _Engine([_Result([(5, 5, "running", None)]), _Result([], rowcount=1)])

    assert await mark_task_retry(engine, claim=claim, error="network") is True

    update_sql = engine.conn.executed[-1][0]
    assert "status = 'failed'" in update_sql


@pytest.mark.asyncio
async def test_positive_event_cancels_only_before_external_boundary_with_fresh_snapshot() -> None:
    now = datetime.now(UTC)
    conn = _Conn(
        [
            _Result([]),  # advisory lock
            _Result([(now - timedelta(seconds=10), 90)]),  # fresh Meta snapshot
            _Result([(101,), (102,)]),  # DB only returns eligible auto pauses
            _Result([], rowcount=0),  # durable cancellation marker for crossed boundary
        ]
    )
    result = await cancel_unstarted_auto_pause(conn, fb_ad_id="238001", now=now)
    assert result.cancelled_task_ids == (101, 102)
    update_sql = conn.executed[2][0]
    assert "requested_by = 'bot_auto_stop'" in update_sql
    assert "payload->>'mutation_kind' = 'pause_ad'" in update_sql
    assert "external_started_at IS NULL" in update_sql
    assert "activate" not in update_sql


@pytest.mark.asyncio
async def test_stale_meta_snapshot_cancels_unstarted_task_and_requests_refresh() -> None:
    now = datetime.now(UTC)
    conn = _Conn(
        [
            _Result([]),
            _Result([(now - timedelta(minutes=10), 90)]),
            _Result([(103,)]),
            _Result([], rowcount=0),
        ]
    )
    result = await cancel_unstarted_auto_pause(conn, fb_ad_id="238001", now=now)
    assert result.cancelled_task_ids == (103,)
    assert result.meta_snapshot_fresh is False
    assert result.needs_scan_refresh is True
    assert len(conn.executed) == 4


@pytest.mark.asyncio
async def test_positive_event_resolves_correlated_incident_in_same_transaction(
    monkeypatch,
) -> None:
    import core.adset_pro.processing as processing

    now = datetime.now(UTC)
    correlation_id = uuid.uuid4()
    lifecycle = AsyncMock()
    monkeypatch.setattr(
        processing,
        "transition_correlated_incident_in_transaction",
        lifecycle,
    )
    conn = _Conn(
        [
            _Result([]),
            _Result([(now, 90)]),
            _Result([(104, correlation_id, {"mutation_kind": "pause_ad"})]),
            _Result([], rowcount=0),
        ]
    )

    result = await cancel_unstarted_auto_pause(conn, fb_ad_id="238001", now=now)

    assert result.cancelled_task_ids == (104,)
    lifecycle.assert_awaited_once_with(
        conn,
        task_id=104,
        correlation_id=correlation_id,
        phase="recovered",
        payload={"mutation_kind": "pause_ad"},
    )
    update_sql = conn.executed[2][0]
    assert "positive_tracker_event_before_external_call" in update_sql
    assert "lease_owner = NULL" in update_sql
    crossed_sql = conn.executed[3][0]
    assert "cancel_requested_at = COALESCE(cancel_requested_at, now())" in crossed_sql
    assert "external_started_at IS NOT NULL" in crossed_sql

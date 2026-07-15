"""Projection ordering, retry and cancellation race contracts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from core.adset_pro.ingest import canonical_event_type
from core.adset_pro.processing import (
    LEGACY_POSITIVE_EVENT_TYPES,
    _canonical_event_type_sql,
    _retry_at,
    attribution_conflicts,
    cancel_unstarted_auto_pause,
    claim_event_tasks,
    confirmed_deposit_at,
    mark_task_retry,
    requeue_aggregation_repair,
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


def test_n1_positive_alias_contract_matches_runtime_canonicalizer() -> None:
    assert all(canonical_event_type(value) is not None for value in LEGACY_POSITIVE_EVENT_TYPES)
    sql = _canonical_event_type_sql("e.event_type")
    assert "e.event_type" in sql
    assert "registration" in sql
    assert "ftd" in sql
    assert "redeposit" in sql


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]]):
        self.rows = rows

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


@pytest.mark.asyncio
async def test_claim_terminalizes_exhausted_tasks_before_claiming() -> None:
    engine = _Engine(
        [
            _Result([]),  # recover provider id
            _Result([]),  # terminally ignore unsupported N-1 rows
            _Result([]),  # enqueue missing N-1 tasks
            _Result([]),  # terminalize exhausted tasks
            _Result([(91,)]),  # claim runnable task
        ]
    )

    claimed = await claim_event_tasks(engine, limit=10)

    assert claimed == [91]
    provider_repair_sql = engine.conn.executed[0][0]
    ignored_sql = engine.conn.executed[1][0]
    enqueue_sql = engine.conn.executed[2][0]
    terminal_sql = engine.conn.executed[3][0]
    assert "provider_event_id = COALESCE" in provider_repair_sql
    assert "attribution_status = 'ignored'" in ignored_sql
    assert "tracker_n1_recovery" in enqueue_sql
    assert "jsonb_build_object" in enqueue_sql
    assert "SET event_type" not in "\n".join(sql for sql, _ in engine.conn.executed)
    assert "status = 'failed'" in terminal_sql
    assert "attempt_count >= max_attempts" in terminal_sql
    assert "next_retry_at = NULL" in terminal_sql


@pytest.mark.asyncio
async def test_infra_failure_on_last_attempt_dead_letters_task() -> None:
    engine = _Engine([_Result([(5, 5, "running")]), _Result([])])

    await mark_task_retry(engine, task_id=91, error="network")

    update_sql = engine.conn.executed[-1][0]
    assert "status = 'failed'" in update_sql
    assert "next_retry_at = NULL" in update_sql


@pytest.mark.asyncio
async def test_aggregation_failure_requeues_succeeded_task_beyond_lookback() -> None:
    engine = _Engine([_Result([(1, 5, "succeeded")]), _Result([])])

    await requeue_aggregation_repair(engine, task_id=91, error="aggregate failed")

    update_sql, params = engine.conn.executed[-1]
    assert "status = 'retrying'" in update_sql
    assert "completed_at = NULL" in update_sql
    assert params["retry_at"] > datetime.now(UTC)


@pytest.mark.asyncio
async def test_aggregation_failure_on_last_attempt_dead_letters_task() -> None:
    engine = _Engine([_Result([(5, 5, "succeeded")]), _Result([])])

    await requeue_aggregation_repair(engine, task_id=91, error="aggregate failed")

    update_sql = engine.conn.executed[-1][0]
    assert "status = 'failed'" in update_sql
    assert "next_retry_at = NULL" in update_sql


@pytest.mark.asyncio
async def test_positive_event_cancels_only_before_external_boundary_with_fresh_snapshot() -> None:
    now = datetime.now(UTC)
    conn = _Conn(
        [
            _Result([]),  # advisory lock
            _Result([(now - timedelta(seconds=10), 90)]),  # fresh Meta snapshot
            _Result([(101,), (102,)]),  # DB only returns eligible auto pauses
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
        ]
    )
    result = await cancel_unstarted_auto_pause(conn, fb_ad_id="238001", now=now)
    assert result.cancelled_task_ids == (103,)
    assert result.meta_snapshot_fresh is False
    assert result.needs_scan_refresh is True
    assert len(conn.executed) == 3

# -*- coding: utf-8 -*-
"""Durable worker liveness: heartbeat vs poll-success (issue #176).

`core/worker_liveness.py` is the PostgreSQL-backed counterpart of the
Prometheus-only gauge in `core/worker_metrics.py`. These tests pin the
behaviours the operator snapshot depends on:

1. A heartbeat-only tick must never erase a worker's last confirmed poll.
2. A real-world connection failure (not just a narrow SQLAlchemy-wrapped one)
   while recording a heartbeat must never crash the caller's real work loop —
   except ``asyncio.CancelledError``, which is how a worker's own
   deadline/shutdown reaches this call and must keep propagating.
3. The poll-staleness threshold is never tighter than the heartbeat threshold,
   so a fully dead process is diagnosed as "offline", not transiently
   misdiagnosed as merely "stalled" for the gap between the two windows.
4. A long-running task keeps ``last_poll_success_at`` fresh via a periodic
   side tick, without swallowing the task's own errors.

Security review (issue #176) reproduced against real asyncpg + SQLAlchemy
2.0.49 that ``ConnectionRefusedError``, ``socket.gaierror`` and
``TimeoutError`` (unified with ``asyncio.TimeoutError`` since 3.11) all
surface unwrapped from ``engine.begin()`` establishing a *new* connection —
none of them is a ``SQLAlchemyError``. The tests below pin the fix.
"""

from __future__ import annotations

import asyncio
import socket

import pytest
from sqlalchemy.exc import OperationalError

from core.worker_liveness import (
    WORKER_POLL_INTERVAL_SECONDS,
    heartbeat_stale_after_seconds,
    poll_heartbeat_while_running,
    poll_stale_after_seconds,
    record_worker_heartbeat,
)


def test_heartbeat_threshold_has_a_floor_independent_of_cadence() -> None:
    # Heartbeat тикает раз в 15с у всех воркеров; порог не должен быть
    # настолько узким, что сетевой джиттер даёт ложный CRITICAL.
    assert heartbeat_stale_after_seconds() >= 60


@pytest.mark.parametrize("worker_name", sorted(WORKER_POLL_INTERVAL_SECONDS))
def test_poll_threshold_scales_with_each_worker_own_cadence(worker_name: str) -> None:
    interval = WORKER_POLL_INTERVAL_SECONDS[worker_name]
    threshold = poll_stale_after_seconds(worker_name)
    assert threshold >= interval
    assert threshold >= 60


def test_poll_threshold_for_unregistered_worker_is_still_a_finite_positive_grace() -> None:
    # Опечатка в имени воркера не должна превращать сигнал мониторинга в KeyError.
    assert poll_stale_after_seconds("unregistered-worker-xyz") >= 60


@pytest.mark.parametrize(
    "worker_name", [*sorted(WORKER_POLL_INTERVAL_SECONDS), "unregistered-worker-xyz"]
)
def test_poll_threshold_is_never_tighter_than_heartbeat_threshold(worker_name: str) -> None:
    """Review issue #176 Б4.

    If a fully dead process's last recorded heartbeat and last recorded poll
    both stopped advancing at the same instant (the normal case, since
    ``poll_success=True`` writes both columns together), a poll threshold
    smaller than the heartbeat threshold crosses first and reports "stalled"
    (process alive, just not polling) instead of "offline" (process dead) for
    the gap between the two windows — the wrong diagnosis in exactly the
    first seconds of the incident this module exists to catch.
    """
    assert poll_stale_after_seconds(worker_name) >= heartbeat_stale_after_seconds()


class _FakeConn:
    def __init__(self, sink: list) -> None:
        self._sink = sink

    async def execute(self, stmt, params):
        self._sink.append((str(stmt), dict(params)))


class _FakeCtx:
    def __init__(self, conn) -> None:
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc_info):
        return False


class _FakeEngine:
    def __init__(self) -> None:
        self.calls: list = []

    def begin(self):
        return _FakeCtx(_FakeConn(self.calls))


@pytest.mark.asyncio
async def test_heartbeat_only_call_does_not_touch_poll_success_column() -> None:
    """Простаивающий воркер обновляет heartbeat каждый тик своего metrics_loop,
    но не должен перетирать last_poll_success_at — иначе healthy idle воркер
    между более редкими рабочими тиками выглядел бы как «завис».
    """
    engine = _FakeEngine()

    await record_worker_heartbeat(engine, "campaign_creator")

    assert len(engine.calls) == 1
    sql, params = engine.calls[0]
    assert "last_poll_success_at" not in sql
    assert params["worker_name"] == "campaign_creator"


@pytest.mark.asyncio
async def test_poll_success_call_advances_both_columns() -> None:
    engine = _FakeEngine()

    await record_worker_heartbeat(engine, "campaign_creator", poll_success=True)

    assert len(engine.calls) == 1
    sql, params = engine.calls[0]
    assert "last_poll_success_at" in sql
    assert "last_heartbeat_at" in sql
    assert params["worker_name"] == "campaign_creator"


class _RaisingConn:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def execute(self, stmt, params):
        raise self._exc


class _RaisingEngine:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def begin(self):
        return _FakeCtx(_RaisingConn(self._exc))


@pytest.mark.asyncio
async def test_record_heartbeat_swallows_transient_db_failure_without_crashing_caller() -> None:
    """DB-запись heartbeat — best-effort вторичный сигнал; транзиентная ошибка
    Postgres не должна ронять реальный рабочий цикл воркера.
    """
    engine = _RaisingEngine(OperationalError("statement", {}, Exception("connection reset")))

    # Не должно бросить исключение наружу.
    await record_worker_heartbeat(engine, "campaign_creator")


@pytest.mark.parametrize(
    ("exc", "case_id"),
    [
        (ConnectionRefusedError("connection refused"), "connection-refused"),
        (socket.gaierror("nodename nor servname provided, or not known"), "dns-failure"),
        (TimeoutError("timed out"), "timeout"),
    ],
)
@pytest.mark.asyncio
async def test_record_heartbeat_swallows_real_connection_failure_modes(
    exc: BaseException, case_id: str
) -> None:
    """Review issue #176 Б1: reproduced against real asyncpg + SQLAlchemy 2.0.49.

    None of these three is a ``sqlalchemy.exc.SQLAlchemyError`` — a narrow
    ``except SQLAlchemyError`` here let all three propagate out of the
    caller's real work loop.
    """
    engine = _RaisingEngine(exc)

    await record_worker_heartbeat(engine, "campaign_creator")


@pytest.mark.asyncio
async def test_cancelled_error_is_never_swallowed() -> None:
    """``asyncio.CancelledError`` is a ``BaseException`` and is how a worker's
    own deadline/shutdown reaches this call (review issue #176 Б1) — it must
    keep propagating, unlike every ``Exception`` above.
    """
    engine = _RaisingEngine(asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await record_worker_heartbeat(engine, "campaign_creator")


@pytest.mark.asyncio
async def test_poll_heartbeat_while_running_keeps_poll_success_fresh_during_a_long_task() -> None:
    """Review issue #176 Б2: a task claimed once and then running for minutes
    (video upload, slow Meta processing) must not look "stalled" just because
    the claim-time poll mark is now older than the threshold. The periodic
    tick inside this context manager is the independent proof that the
    worker is genuinely busy, not merely claimed once a while ago.
    """
    engine = _FakeEngine()

    async with poll_heartbeat_while_running(engine, "campaign_creator", interval_seconds=0.01):
        await asyncio.sleep(0.05)

    assert len(engine.calls) >= 1
    _, params = engine.calls[0]
    assert params["worker_name"] == "campaign_creator"


@pytest.mark.asyncio
async def test_poll_heartbeat_while_running_does_not_swallow_the_wrapped_task_error() -> None:
    """The periodic side tick must be fully transparent to the real task it
    runs alongside — it never touches lease/fencing/control-plane state and
    must never mask a genuine task failure.
    """
    engine = _FakeEngine()

    with pytest.raises(RuntimeError, match="boom"):
        async with poll_heartbeat_while_running(engine, "campaign_creator", interval_seconds=1.0):
            raise RuntimeError("boom")

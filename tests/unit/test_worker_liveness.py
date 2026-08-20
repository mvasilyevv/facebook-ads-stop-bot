# -*- coding: utf-8 -*-
"""Durable worker liveness: heartbeat vs poll-success (issue #176).

`core/worker_heartbeat.py` is the PostgreSQL-backed counterpart of the
Prometheus-only gauge in `core/worker_metrics.py`. These tests pin the two
behaviours the operator snapshot depends on: (1) a heartbeat-only tick must
never erase a worker's last confirmed poll, and (2) a transient PostgreSQL
failure while recording a heartbeat must never crash the caller's real work
loop.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from core.worker_liveness import (
    WORKER_POLL_INTERVAL_SECONDS,
    heartbeat_stale_after_seconds,
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


@pytest.mark.asyncio
async def test_record_heartbeat_swallows_transient_db_failure_without_crashing_caller() -> None:
    """DB-запись heartbeat — best-effort вторичный сигнал; транзиентная ошибка
    Postgres не должна ронять реальный рабочий цикл воркера.
    """

    class _FailingConn:
        async def execute(self, stmt, params):
            raise OperationalError("statement", {}, Exception("connection reset"))

    class _FailingEngine:
        def begin(self):
            return _FakeCtx(_FailingConn())

    # Не должно бросить исключение наружу.
    await record_worker_heartbeat(_FailingEngine(), "campaign_creator")

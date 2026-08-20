# -*- coding: utf-8 -*-
"""Durable worker liveness against a real PostgreSQL (issue #176 review, Т1).

`tests/unit/test_worker_liveness.py` proves the write function branches on
``poll_success`` using a fake connection that records SQL text — that is
useful for pure branching logic, but it cannot prove the SQL is correct: the
table name, the ``ON CONFLICT (worker_name)`` upsert target, and the central
invariant "a heartbeat-only tick never erases a confirmed poll success" are
only true if PostgreSQL actually accepts and executes the statement that way.
This file closes that gap against a real, disposable database (see
``tests/integration/conftest.py`` — never the shared dev/CI Postgres).
"""

from __future__ import annotations

from sqlalchemy import text

from core.operator.queries import fetch_worker_heartbeats
from core.worker_liveness import record_worker_heartbeat


async def test_repeated_heartbeat_upserts_one_row_not_two(pg_engine) -> None:
    """Proves ``ON CONFLICT (worker_name) DO UPDATE`` actually targets the
    real unique constraint, not just that the fake test double parsed the
    SQL text containing that phrase.
    """
    await record_worker_heartbeat(pg_engine, "campaign_creator")
    await record_worker_heartbeat(pg_engine, "campaign_creator")

    async with pg_engine.connect() as conn:
        count = await conn.scalar(
            text("SELECT COUNT(*) FROM worker_heartbeats WHERE worker_name = 'campaign_creator'")
        )
    assert count == 1


async def test_heartbeat_only_tick_never_erases_a_confirmed_poll_success(pg_engine) -> None:
    """An idle worker ticks a heartbeat-only write every ~15s between its
    rarer real work-loop ticks. If that tick reset ``last_poll_success_at``,
    a healthy idle worker would look "stalled" in the gap between work ticks
    — this is the exact invariant a fake connection cannot prove.
    """
    await record_worker_heartbeat(pg_engine, "reconciler", poll_success=True)
    async with pg_engine.connect() as conn:
        confirmed_poll_at = await conn.scalar(
            text(
                "SELECT last_poll_success_at FROM worker_heartbeats "
                "WHERE worker_name = 'reconciler'"
            )
        )
    assert confirmed_poll_at is not None

    await record_worker_heartbeat(pg_engine, "reconciler")  # heartbeat-only, no poll_success

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT last_heartbeat_at, last_poll_success_at FROM worker_heartbeats "
                    "WHERE worker_name = 'reconciler'"
                )
            )
        ).one()
    assert row.last_heartbeat_at is not None
    assert row.last_poll_success_at == confirmed_poll_at


async def test_written_row_round_trips_through_the_operator_read_side(pg_engine) -> None:
    """Closes the loop end-to-end: the same table/columns the write side
    upserts into are what the operator snapshot's read query selects from.
    """
    await record_worker_heartbeat(pg_engine, "health_watchdog", poll_success=True)

    rows = await fetch_worker_heartbeats(pg_engine)

    by_name = {row["worker_name"]: row for row in rows}
    assert "health_watchdog" in by_name
    assert by_name["health_watchdog"]["last_heartbeat_at"] is not None
    assert by_name["health_watchdog"]["last_poll_success_at"] is not None

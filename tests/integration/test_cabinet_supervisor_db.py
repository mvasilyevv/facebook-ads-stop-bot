"""Real PostgreSQL contracts for per-cabinet actor ownership."""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.deadlines import remaining_deadline_seconds
from core.observer.cabinet_supervisor import (
    CabinetLease,
    CabinetSupervisor,
    acquire_cabinet_lease,
)
from core.observer.writers import CabinetFenceRejected, _lock_and_assert_cabinet_fence


@pytest_asyncio.fixture
async def clean_cabinet_runtime(pg_engine):
    account = f"act_sup_{uuid.uuid4().hex[:20]}"
    yield account
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM cabinet_runtime WHERE ad_account_id = :account"),
            {"account": account},
        )


@pytest.mark.asyncio
async def test_session_lock_prevents_overlapping_cabinet_actors(
    pg_engine,
    clean_cabinet_runtime,
) -> None:
    account = clean_cabinet_runtime
    entered = asyncio.Event()
    release = asyncio.Event()
    observed_leases: list[CabinetLease] = []
    observed_deadlines: list[float] = []

    async def blocked_actor(
        account_id: str,
        _index: int,
        lease: CabinetLease,
    ) -> dict[str, object]:
        assert account_id == account
        observed_leases.append(lease)
        remaining = remaining_deadline_seconds()
        assert remaining is not None
        observed_deadlines.append(remaining)
        entered.set()
        await release.wait()
        return {"ad_account_id": account_id, "outcome": "success", "error": None}

    async def fast_actor(
        account_id: str,
        _index: int,
        lease: CabinetLease,
    ) -> dict[str, object]:
        observed_leases.append(lease)
        return {"ad_account_id": account_id, "outcome": "success", "error": None}

    first = CabinetSupervisor(
        pg_engine,
        owner_instance=uuid.uuid4(),
        scan_deadline_seconds=10,
        lease_ttl_seconds=20,
    )
    contender = CabinetSupervisor(
        pg_engine,
        owner_instance=uuid.uuid4(),
        scan_deadline_seconds=10,
        lease_ttl_seconds=20,
    )

    first_run = asyncio.create_task(first.run_cycle([account], blocked_actor))
    await asyncio.wait_for(entered.wait(), timeout=3)
    overlapping = await asyncio.wait_for(
        contender.run_cycle([account], fast_actor),
        timeout=3,
    )

    assert overlapping == [
        {
            "ad_account_id": account,
            "outcome": "skipped",
            "error": "cabinet_actor_lock_held",
        }
    ]
    assert len(observed_leases) == 1

    release.set()
    completed = await asyncio.wait_for(first_run, timeout=3)
    assert completed[0]["outcome"] == "success"

    # Closing the first actor connection releases the session lock, allowing a
    # later cycle to take a strictly newer fencing token.
    later = await asyncio.wait_for(contender.run_cycle([account], fast_actor), timeout=3)
    assert later[0]["outcome"] == "success"
    assert len(observed_leases) == 2
    assert observed_deadlines and all(0 < value <= 10 for value in observed_deadlines)
    assert observed_leases[1].lease_token > observed_leases[0].lease_token


@pytest.mark.asyncio
async def test_confirmed_empty_clears_actor_error_and_records_snapshot(
    pg_engine,
    clean_cabinet_runtime,
) -> None:
    account = clean_cabinet_runtime
    supervisor = CabinetSupervisor(
        pg_engine,
        owner_instance=uuid.uuid4(),
        scan_deadline_seconds=10,
        lease_ttl_seconds=20,
    )

    async def failed_actor(
        account_id: str,
        _index: int,
        _lease: CabinetLease,
    ) -> dict[str, object]:
        return {
            "ad_account_id": account_id,
            "outcome": "error",
            "error": "scanner_unavailable",
        }

    failed = await supervisor.run_cycle([account], failed_actor)
    assert failed[0]["outcome"] == "error"
    async with pg_engine.connect() as conn:
        failed_state = (
            await conn.execute(
                text(
                    "SELECT last_snapshot_at, last_error_code "
                    "FROM cabinet_runtime WHERE ad_account_id = :account"
                ),
                {"account": account},
            )
        ).one()
    assert failed_state.last_snapshot_at is None
    assert failed_state.last_error_code == "scanner_unavailable"

    async def known_empty_actor(
        account_id: str,
        _index: int,
        _lease: CabinetLease,
    ) -> dict[str, object]:
        return {
            "ad_account_id": account_id,
            "outcome": "empty",
            "error": "no_active_ads",
        }

    empty = await supervisor.run_cycle([account], known_empty_actor)
    assert empty[0]["outcome"] == "empty"
    async with pg_engine.connect() as conn:
        recovered_state = (
            await conn.execute(
                text(
                    "SELECT last_snapshot_at, last_error_code "
                    "FROM cabinet_runtime WHERE ad_account_id = :account"
                ),
                {"account": account},
            )
        ).one()
    assert recovered_state.last_snapshot_at is not None
    assert recovered_state.last_error_code is None


@pytest.mark.asyncio
async def test_observer_write_fence_rejects_stale_actor_in_write_transaction(
    pg_engine,
    clean_cabinet_runtime,
) -> None:
    account = clean_cabinet_runtime
    first = await acquire_cabinet_lease(
        pg_engine,
        ad_account_id=account,
        owner_instance=uuid.uuid4(),
        ttl_seconds=30,
    )
    assert first is not None

    # Model a crashed actor whose row lease expired, followed by takeover.
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE cabinet_runtime
                SET lease_expires_at = NOW() - interval '1 second'
                WHERE ad_account_id = :account
                """
            ),
            {"account": account},
        )
    successor = await acquire_cabinet_lease(
        pg_engine,
        ad_account_id=account,
        owner_instance=uuid.uuid4(),
        ttl_seconds=30,
    )
    assert successor is not None
    assert successor.lease_token > first.lease_token

    async with pg_engine.begin() as conn:
        with pytest.raises(CabinetFenceRejected):
            await _lock_and_assert_cabinet_fence(conn, first)

    async with pg_engine.begin() as conn:
        await _lock_and_assert_cabinet_fence(conn, successor)

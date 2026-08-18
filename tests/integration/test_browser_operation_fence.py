from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text

import core.meta_api.account_tz as account_tz
from core.tasks.browser_fence import (
    BrowserExclusiveMaintenance,
    BrowserMaintenanceGuard,
    BrowserMaintenanceOwnerInvalid,
    BrowserOperationBlocked,
    BrowserOperationFence,
)
from core.tasks.queue import claim_next_task, create_task, mark_succeeded


@pytest_asyncio.fixture(autouse=True)
async def clean_browser_fences(pg_engine):
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM task_queue WHERE idempotency_key LIKE 'browser-fence-%'")
        )
        await conn.execute(text("DELETE FROM browser_operation_leases"))
        await conn.execute(text("DELETE FROM system_config WHERE key = 'browser_maintenance'"))
    yield
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM task_queue WHERE idempotency_key LIKE 'browser-fence-%'")
        )
        await conn.execute(text("DELETE FROM browser_operation_leases"))
        await conn.execute(text("DELETE FROM system_config WHERE key = 'browser_maintenance'"))


@pytest.mark.asyncio
async def test_shared_browser_operation_is_durable_and_released(pg_engine) -> None:
    async with BrowserOperationFence(
        pg_engine,
        operation_kind="campaign_refresh",
        target="active_offers",
    ) as fence:
        await fence.assert_held()
        async with pg_engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT owner, operation_kind, target
                        FROM browser_operation_leases
                        WHERE operation_id = :operation_id
                        """
                    ),
                    {"operation_id": fence.operation_id},
                )
            ).one()
        assert row.owner == fence.owner
        assert row.operation_kind == "campaign_refresh"
        assert row.target == "active_offers"

    async with pg_engine.connect() as conn:
        remaining = int(
            (
                await conn.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM browser_operation_leases
                        WHERE operation_id = :operation_id
                        """
                    ),
                    {"operation_id": fence.operation_id},
                )
            ).scalar_one()
        )
    assert remaining == 0


@pytest.mark.asyncio
async def test_active_maintenance_blocks_new_shared_operation(pg_engine) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO system_config (key, value, description)
                VALUES (
                  'browser_maintenance',
                  jsonb_build_object(
                    'owner', CAST(:owner AS text),
                    'expires_at', clock_timestamp() + interval '5 minutes'
                  ),
                  'test'
                )
                """
            ),
            {"owner": uuid.uuid4().hex},
        )

    with pytest.raises(BrowserOperationBlocked):
        async with BrowserOperationFence(
            pg_engine,
            operation_kind="campaign_refresh",
        ):
            raise AssertionError("blocked operation must not enter")


@pytest.mark.asyncio
async def test_maintenance_sees_and_drains_account_context_refresh(
    pg_engine,
    monkeypatch,
) -> None:
    graph_started = asyncio.Event()
    release_graph = asyncio.Event()
    persistence_started = asyncio.Event()
    release_persistence = asyncio.Event()

    class BlockingClient:
        async def execute_graph_call(self, **kwargs):
            assert kwargs == {
                "method": "GET",
                "endpoint": "/act_123456",
                "query_params": {"fields": "timezone_name,currency"},
                "ad_account_id": "123456",
            }
            graph_started.set()
            await release_graph.wait()
            return {"timezone_name": "Europe/Kaliningrad", "currency": "USD"}

    async def active_accounts(_engine):
        return ["123456"]

    async def persist_context(_engine, **kwargs):
        assert kwargs == {
            "account_id": "123456",
            "timezone_name": "Europe/Kaliningrad",
            "currency": "USD",
        }
        persistence_started.set()
        await release_persistence.wait()
        return True

    monkeypatch.setattr(account_tz, "resolve_scan_account_ids", active_accounts)
    monkeypatch.setattr(account_tz, "persist_account_context", persist_context)

    refresh = asyncio.create_task(account_tz.refresh_account_timezones(pg_engine, BlockingClient()))
    await asyncio.wait_for(graph_started.wait(), timeout=2)

    async with pg_engine.connect() as conn:
        lease = (
            await conn.execute(
                text(
                    """
                    SELECT operation_kind, target
                    FROM browser_operation_leases
                    WHERE operation_kind = 'account_context_refresh'
                    """
                )
            )
        ).one()
    assert lease.operation_kind == "account_context_refresh"
    assert lease.target == "123456"

    release_graph.set()
    await asyncio.wait_for(persistence_started.wait(), timeout=2)

    exclusive = BrowserExclusiveMaintenance(
        pg_engine,
        operation_kind="vision_reconnect",
        drain_seconds=5,
    )
    entering = asyncio.create_task(exclusive.__aenter__())
    for _ in range(50):
        async with pg_engine.connect() as conn:
            gate_exists = bool(
                (
                    await conn.execute(
                        text(
                            """
                            SELECT EXISTS (
                              SELECT 1
                              FROM system_config
                              WHERE key = 'browser_maintenance'
                                AND (value->>'expires_at')::timestamptz
                                  > clock_timestamp()
                            )
                            """
                        )
                    )
                ).scalar_one()
            )
        if gate_exists:
            break
        await asyncio.sleep(0.02)
    assert gate_exists
    assert not entering.done()

    release_persistence.set()
    assert await asyncio.wait_for(refresh, timeout=2) == 1
    entered = await asyncio.wait_for(entering, timeout=2)
    assert entered is exclusive
    await exclusive.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_account_context_is_not_persisted_after_refresh_fence_loss(
    pg_engine,
    monkeypatch,
) -> None:
    graph_started = asyncio.Event()
    release_graph = asyncio.Event()

    class BlockingClient:
        async def execute_graph_call(self, **_kwargs):
            graph_started.set()
            await release_graph.wait()
            return {"timezone_name": "Europe/Kaliningrad", "currency": "USD"}

    async def active_accounts(_engine):
        return ["123456"]

    persist_context = AsyncMock(return_value=True)
    monkeypatch.setattr(account_tz, "resolve_scan_account_ids", active_accounts)
    monkeypatch.setattr(account_tz, "persist_account_context", persist_context)

    refresh = asyncio.create_task(account_tz.refresh_account_timezones(pg_engine, BlockingClient()))
    await asyncio.wait_for(graph_started.wait(), timeout=2)

    async with pg_engine.begin() as conn:
        deleted = (
            await conn.execute(
                text(
                    """
                    DELETE FROM browser_operation_leases
                    WHERE operation_kind = 'account_context_refresh'
                      AND target = '123456'
                    RETURNING operation_id
                    """
                )
            )
        ).first()
    assert deleted is not None

    release_graph.set()
    assert await asyncio.wait_for(refresh, timeout=2) == 0
    persist_context.assert_not_awaited()


@pytest.mark.asyncio
async def test_exclusive_fence_blocks_claims_and_drains_existing_task(
    pg_engine,
) -> None:
    first_id = await create_task(
        pg_engine,
        task_type="observer_scan",
        idempotency_key=f"browser-fence-running-{uuid.uuid4().hex}",
        payload={"reason": "test"},
        requested_by="test",
        lane="background",
    )
    first = await claim_next_task(
        pg_engine,
        task_type="observer_scan",
        lanes=("background",),
    )
    assert first.task is not None

    exclusive = BrowserExclusiveMaintenance(
        pg_engine,
        operation_kind="vision_reconnect",
        drain_seconds=5,
    )
    entering = asyncio.create_task(exclusive.__aenter__())

    for _ in range(50):
        async with pg_engine.connect() as conn:
            gate_exists = bool(
                (
                    await conn.execute(
                        text(
                            """
                            SELECT EXISTS (
                              SELECT 1
                              FROM system_config
                              WHERE key = 'browser_maintenance'
                                AND (value->>'expires_at')::timestamptz
                                  > clock_timestamp()
                            )
                            """
                        )
                    )
                ).scalar_one()
            )
        if gate_exists:
            break
        await asyncio.sleep(0.02)
    assert gate_exists
    assert not entering.done()

    await create_task(
        pg_engine,
        task_type="observer_scan",
        idempotency_key=f"browser-fence-blocked-{uuid.uuid4().hex}",
        payload={"reason": "test"},
        requested_by="test",
        lane="background",
    )
    blocked = await claim_next_task(
        pg_engine,
        task_type="observer_scan",
        lanes=("background",),
    )
    assert blocked.task is None

    assert first.task.lease_owner is not None
    assert await mark_succeeded(
        pg_engine,
        task_id=first_id,
        result={"outcome": "CONFIRMED"},
        lease_owner=first.task.lease_owner,
        lease_token=first.task.lease_token,
    )
    entered = await asyncio.wait_for(entering, timeout=2)
    assert entered is exclusive
    await exclusive.assert_held()
    await exclusive.__aexit__(None, None, None)

    released = await claim_next_task(
        pg_engine,
        task_type="observer_scan",
        lanes=("background",),
    )
    assert released.task is not None


@pytest.mark.asyncio
async def test_platform_guard_requires_and_renews_exact_owner(pg_engine) -> None:
    owner = uuid.uuid4().hex
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO system_config (key, value, description)
                VALUES (
                  'browser_maintenance',
                  jsonb_build_object(
                    'owner', CAST(:owner AS text),
                    'expires_at', clock_timestamp() + interval '5 seconds'
                  ),
                  'test'
                )
                """
            ),
            {"owner": owner},
        )

    async with BrowserMaintenanceGuard(pg_engine, owner) as guard:
        await guard.assert_held()

    with pytest.raises(BrowserMaintenanceOwnerInvalid):
        async with BrowserMaintenanceGuard(pg_engine, uuid.uuid4().hex):
            raise AssertionError("wrong owner must not enter")


@pytest.mark.asyncio
async def test_platform_guard_rejects_correct_owner_until_browser_work_drains(
    pg_engine,
) -> None:
    task_id = await create_task(
        pg_engine,
        task_type="observer_scan",
        idempotency_key=f"browser-fence-guard-running-{uuid.uuid4().hex}",
        payload={"reason": "test"},
        requested_by="test",
        lane="background",
    )
    claim = await claim_next_task(
        pg_engine,
        task_type="observer_scan",
        lanes=("background",),
    )
    assert claim.task is not None

    owner = uuid.uuid4().hex
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO system_config (key, value, description)
                VALUES (
                  'browser_maintenance',
                  jsonb_build_object(
                    'owner', CAST(:owner AS text),
                    'expires_at', clock_timestamp() + interval '5 minutes'
                  ),
                  'test'
                )
                """
            ),
            {"owner": owner},
        )

    with pytest.raises(BrowserOperationBlocked):
        async with BrowserMaintenanceGuard(pg_engine, owner):
            raise AssertionError("active browser work must prevent maintenance entry")

    assert claim.task.lease_owner is not None
    assert await mark_succeeded(
        pg_engine,
        task_id=task_id,
        result={"outcome": "CONFIRMED"},
        lease_owner=claim.task.lease_owner,
        lease_token=claim.task.lease_token,
    )

    async with BrowserMaintenanceGuard(pg_engine, owner) as guard:
        await guard.assert_held()

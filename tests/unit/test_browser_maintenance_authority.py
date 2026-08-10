from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.tasks import browser_fence
from core.tasks.browser_fence import (
    BrowserMaintenanceCapabilityAuthorityUnavailableError,
    BrowserMaintenanceCapabilityConsume,
    BrowserMaintenanceCapabilityConsumeDeniedError,
    consume_browser_maintenance_capability,
)


def _capability(**updates) -> BrowserMaintenanceCapabilityConsume:
    values = {
        "profile_id": "profile-1",
        "owner": "a" * 32,
        "expires_at_epoch": 1_900_000_000,
        "nonce": "b" * 32,
    }
    values.update(updates)
    return BrowserMaintenanceCapabilityConsume(**values)


def _engine(consumed_nonce: str | None) -> MagicMock:
    set_result = MagicMock()
    update_result = MagicMock()
    update_result.scalar_one_or_none.return_value = consumed_nonce
    connection = MagicMock()
    connection.execute = AsyncMock(side_effect=[set_result, update_result])
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=None)
    engine = MagicMock()
    engine.begin.return_value = transaction
    engine._connection = connection
    engine._transaction = transaction
    return engine


@pytest.mark.asyncio
async def test_maintenance_consume_commits_one_atomic_owner_bound_cas() -> None:
    engine = _engine("b" * 32)

    await consume_browser_maintenance_capability(engine, _capability())

    assert engine._connection.execute.await_count == 2
    params = engine._connection.execute.await_args_list[1].args[1]
    assert params == {
        "profile_id": "profile-1",
        "owner": "a" * 32,
        "expires_at_epoch": 1_900_000_000,
        "nonce": "b" * 32,
        "max_capability_ttl_seconds": 35,
    }
    engine._transaction.__aexit__.assert_awaited_once()


def test_maintenance_consume_sql_requires_absent_marker_and_live_bounded_owner() -> None:
    sql = str(browser_fence._CONSUME_MAINTENANCE_CAPABILITY_SQL)

    assert "value->>'owner' = :owner" in sql
    assert "(value->>'expires_at')::timestamptz > clock_timestamp()" in sql
    assert "NOT (value ? 'consumed_capability_nonce')" in sql
    assert "to_timestamp(:expires_at_epoch) > clock_timestamp()" in sql
    assert "make_interval(secs => :max_capability_ttl_seconds)" in sql
    assert (
        "to_timestamp(:expires_at_epoch)\n            <= (value->>'expires_at')::timestamptz"
    ) in sql


@pytest.mark.asyncio
async def test_maintenance_consume_denies_replay_wrong_owner_or_expiry() -> None:
    with pytest.raises(
        BrowserMaintenanceCapabilityConsumeDeniedError,
        match="already consumed",
    ):
        await consume_browser_maintenance_capability(
            _engine(None),
            _capability(),
        )


@pytest.mark.asyncio
async def test_maintenance_consume_wraps_database_failure() -> None:
    engine = MagicMock()
    engine.begin.return_value.__aenter__ = AsyncMock(
        side_effect=RuntimeError("database unavailable")
    )
    engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)

    with pytest.raises(BrowserMaintenanceCapabilityAuthorityUnavailableError) as caught:
        await consume_browser_maintenance_capability(engine, _capability())

    assert isinstance(caught.value.__cause__, RuntimeError)


@pytest.mark.asyncio
async def test_maintenance_consume_rejects_malformed_binding_before_database() -> None:
    engine = MagicMock()
    with pytest.raises(BrowserMaintenanceCapabilityConsumeDeniedError):
        await consume_browser_maintenance_capability(
            engine,
            _capability(owner="not-an-owner"),
        )
    engine.begin.assert_not_called()

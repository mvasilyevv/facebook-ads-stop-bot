from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.meta_api.client import BROWSER_CONTRACT_VERSION
from core.meta_api.operation_authority import (
    BrowserCapabilityAuthorityUnavailableError,
    BrowserCapabilityConsume,
    BrowserCapabilityConsumeDeniedError,
    consume_pending_browser_capability,
)


def _capability(**updates) -> BrowserCapabilityConsume:
    values = {
        "browser_contract_version": BROWSER_CONTRACT_VERSION,
        "rpc": "execute_graph_call",
        "operation": "POST:/111|q=" + ("a" * 64) + "|b=" + ("b" * 64),
        "session_id": "session-1",
        "vision_profile_id": "profile-1",
        "ad_account_id": "123",
        "caller": "autopause",
        "task_id": 1842,
        "lease_owner": uuid.UUID("2c5114e4-d921-4fc5-9986-18831eb56d5d"),
        "lease_token": 7,
        "expires_at_epoch": 1_900_000_000,
        "nonce": "c" * 32,
    }
    values.update(updates)
    return BrowserCapabilityConsume(**values)


def _engine(consumed_at) -> MagicMock:
    set_result = MagicMock()
    update_result = MagicMock()
    update_result.scalar_one_or_none.return_value = consumed_at
    connection = MagicMock()
    connection.execute = AsyncMock(side_effect=[set_result, update_result])
    engine = MagicMock()
    engine.begin.return_value.__aenter__ = AsyncMock(return_value=connection)
    engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
    engine._connection = connection
    return engine


@pytest.mark.asyncio
async def test_durable_capability_consume_is_one_atomic_cas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "core.meta_api.operation_authority.time.time",
        lambda: 1_800_000_000,
    )
    engine = _engine(datetime.now(UTC))
    await consume_pending_browser_capability(engine, _capability())

    assert engine._connection.execute.await_count == 2
    params = engine._connection.execute.await_args_list[1].args[1]
    assert params["task_id"] == 1842
    assert params["lease_token"] == 7
    assert params["browser_contract_version"] == BROWSER_CONTRACT_VERSION
    assert len(params["nonce_sha256"]) == 32
    assert len(params["capability_digest"]) == 32
    assert len(params["operation_digest"]) == 32


@pytest.mark.asyncio
async def test_durable_capability_consume_denies_replay_or_binding_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "core.meta_api.operation_authority.time.time",
        lambda: 1_800_000_000,
    )
    with pytest.raises(BrowserCapabilityConsumeDeniedError, match="already consumed"):
        await consume_pending_browser_capability(_engine(None), _capability())


@pytest.mark.asyncio
async def test_durable_capability_consume_wraps_database_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "core.meta_api.operation_authority.time.time",
        lambda: 1_800_000_000,
    )
    engine = MagicMock()
    engine.begin.return_value.__aenter__ = AsyncMock(
        side_effect=RuntimeError("database unavailable")
    )
    engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
    with pytest.raises(BrowserCapabilityAuthorityUnavailableError) as caught:
        await consume_pending_browser_capability(engine, _capability())
    assert isinstance(caught.value.__cause__, RuntimeError)


@pytest.mark.asyncio
async def test_durable_capability_consume_rejects_expired_before_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "core.meta_api.operation_authority.time.time",
        lambda: 1_900_000_001,
    )
    engine = MagicMock()
    with pytest.raises(BrowserCapabilityConsumeDeniedError, match="expired"):
        await consume_pending_browser_capability(engine, _capability())
    engine.begin.assert_not_called()


@pytest.mark.asyncio
async def test_durable_capability_consume_rejects_wrong_contract_before_database() -> None:
    engine = MagicMock()
    with pytest.raises(
        BrowserCapabilityConsumeDeniedError,
        match="malformed",
    ):
        await consume_pending_browser_capability(
            engine,
            _capability(browser_contract_version=BROWSER_CONTRACT_VERSION - 1),
        )
    engine.begin.assert_not_called()

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from apps.api.routers.v1 import browser_operations_internal as route
from core.meta_api.operation_authority import (
    BrowserCapabilityAuthorityUnavailableError,
    BrowserCapabilityConsumeDeniedError,
)
from core.tasks.browser_fence import (
    BrowserMaintenanceCapabilityAuthorityUnavailableError,
    BrowserMaintenanceCapabilityConsumeDeniedError,
)

_TOKEN = "browser-authority-route-token-" + ("x" * 48)


def _request() -> route.BrowserCapabilityConsumeRequest:
    return route.BrowserCapabilityConsumeRequest(
        browser_contract_version=5,
        rpc="execute_graph_call",
        operation="POST:/111|q=" + ("a" * 64) + "|b=" + ("b" * 64),
        session_id="session-1",
        vision_profile_id="profile-1",
        ad_account_id="123",
        authorized_caller="autopause",
        task_id=1842,
        lease_owner=uuid.UUID("2c5114e4-d921-4fc5-9986-18831eb56d5d"),
        lease_token=7,
        capability_expires_at=1_900_000_000,
        capability_nonce="c" * 32,
    )


def _maintenance_request() -> route.BrowserMaintenanceCapabilityConsumeRequest:
    return route.BrowserMaintenanceCapabilityConsumeRequest(
        rpc="recover_browser_profile",
        vision_profile_id="profile-1",
        maintenance_owner="a" * 32,
        capability_expires_at=1_900_000_000,
        capability_nonce="b" * 32,
    )


def test_browser_consumer_token_uses_constant_time_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_AUTHORITY_CONSUMER_TOKEN", _TOKEN)
    compared: list[tuple[str, str]] = []

    def _compare(candidate: str, expected: str) -> bool:
        compared.append((candidate, expected))
        return candidate == expected

    monkeypatch.setattr(route.secrets, "compare_digest", _compare)
    route._authorize_browser_consumer(_TOKEN)
    assert compared == [(_TOKEN, _TOKEN)]

    with pytest.raises(HTTPException) as caught:
        route._authorize_browser_consumer("wrong")
    assert caught.value.status_code == 401
    assert compared[-1] == ("wrong", _TOKEN)


def test_browser_consumer_fails_closed_without_server_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BROWSER_AUTHORITY_CONSUMER_TOKEN", raising=False)
    with pytest.raises(HTTPException) as caught:
        route._authorize_browser_consumer(None)
    assert caught.value.status_code == 503


@pytest.mark.asyncio
async def test_browser_consume_route_exposes_only_boundary_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_AUTHORITY_CONSUMER_TOKEN", _TOKEN)
    consume = AsyncMock(return_value=None)
    monkeypatch.setattr(route, "consume_pending_browser_capability", consume)

    response = await route.consume_browser_operation(
        _request(),
        MagicMock(),
        _TOKEN,
    )
    assert response.status_code == 204
    capability = consume.await_args.args[1]
    assert capability.task_id == 1842
    assert capability.ad_account_id == "123"
    assert capability.browser_contract_version == 5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (BrowserCapabilityConsumeDeniedError("private row detail"), 409),
        (BrowserCapabilityAuthorityUnavailableError("private DB detail"), 503),
    ],
)
async def test_browser_consume_route_never_returns_private_authority_details(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_status: int,
) -> None:
    monkeypatch.setenv("BROWSER_AUTHORITY_CONSUMER_TOKEN", _TOKEN)
    monkeypatch.setattr(
        route,
        "consume_pending_browser_capability",
        AsyncMock(side_effect=error),
    )

    with pytest.raises(HTTPException) as caught:
        await route.consume_browser_operation(
            _request(),
            MagicMock(),
            _TOKEN,
        )
    assert caught.value.status_code == expected_status
    assert "private" not in str(caught.value.detail)


@pytest.mark.asyncio
async def test_browser_maintenance_consume_returns_204_only_after_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_AUTHORITY_CONSUMER_TOKEN", _TOKEN)
    consume = AsyncMock(return_value=None)
    monkeypatch.setattr(
        route,
        "consume_browser_maintenance_capability",
        consume,
    )

    response = await route.consume_browser_maintenance(
        _maintenance_request(),
        MagicMock(),
        _TOKEN,
    )

    assert response.status_code == 204
    capability = consume.await_args.args[1]
    assert capability.owner == "a" * 32
    assert capability.profile_id == "profile-1"
    assert capability.nonce == "b" * 32


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (BrowserMaintenanceCapabilityConsumeDeniedError("private row"), 409),
        (
            BrowserMaintenanceCapabilityAuthorityUnavailableError("private DB"),
            503,
        ),
    ],
)
async def test_browser_maintenance_consume_failures_are_closed_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_status: int,
) -> None:
    monkeypatch.setenv("BROWSER_AUTHORITY_CONSUMER_TOKEN", _TOKEN)
    monkeypatch.setattr(
        route,
        "consume_browser_maintenance_capability",
        AsyncMock(side_effect=error),
    )

    with pytest.raises(HTTPException) as caught:
        await route.consume_browser_maintenance(
            _maintenance_request(),
            MagicMock(),
            _TOKEN,
        )
    assert caught.value.status_code == expected_status
    assert "private" not in str(caught.value.detail)

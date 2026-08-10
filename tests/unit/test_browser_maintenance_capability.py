from __future__ import annotations

import hashlib
import hmac
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig


@pytest.mark.asyncio
async def test_recovery_capability_binds_owner_credentials_endpoint_and_uses_lifecycle_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "maintenance-secret-" + ("x" * 48)
    monkeypatch.setenv("BROWSER_MAINTENANCE_CAPABILITY_SECRET", secret)
    monkeypatch.setattr("clients.python_grpc.client.time.time", lambda: 1_800_000_000)
    monkeypatch.setattr(
        "clients.python_grpc.client.secrets.token_hex",
        lambda _length: "b" * 32,
    )

    recover = AsyncMock(
        return_value=SimpleNamespace(
            session_id="session-recovered",
            profile=SimpleNamespace(cdp_port=9222),
        )
    )
    client = BrowserAgentClient(
        BrowserAgentConfig(
            vision_x_token="vision-token",
            vision_api_url="http://127.0.0.1:3030",
            vision_profile_id="profile-1",
            vision_folder_id="folder-1",
        )
    )
    client._browser_stub = SimpleNamespace(
        RecoverBrowserProfileUnderMaintenance=recover,
    )

    session_id = await client.recover_browser_profile_under_maintenance(
        maintenance_owner="a" * 32,
    )

    assert session_id == "session-recovered"
    request = recover.await_args.args[0]
    assert recover.await_args.kwargs["timeout"] == 120.0
    assert request.capability_expires_at == 1_800_000_030
    assert request.capability_nonce == "b" * 32
    assert request.maintenance_owner == "a" * 32
    payload = "\n".join(
        (
            "recover_browser_profile/v1",
            "profile-1",
            "a" * 32,
            "1800000030",
            "b" * 32,
            "http://127.0.0.1:3030",
            "folder-1",
            hashlib.sha256(b"vision-token").hexdigest(),
        )
    ).encode()
    assert (
        request.capability_signature
        == hmac.new(
            secret.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("owner", "secret", "message"),
    [
        ("not-an-owner", "x" * 64, "valid browser maintenance owner"),
        ("a" * 32, "", "capability secret is unavailable"),
        ("a" * 32, "short", "capability secret is unavailable"),
    ],
)
async def test_recovery_rejects_invalid_authority_before_rpc(
    monkeypatch: pytest.MonkeyPatch,
    owner: str,
    secret: str,
    message: str,
) -> None:
    monkeypatch.setenv("BROWSER_MAINTENANCE_CAPABILITY_SECRET", secret)
    recover = AsyncMock()
    client = BrowserAgentClient(BrowserAgentConfig())
    client._browser_stub = SimpleNamespace(
        RecoverBrowserProfileUnderMaintenance=recover,
    )

    with pytest.raises((ValueError, RuntimeError), match=message):
        await client.recover_browser_profile_under_maintenance(
            maintenance_owner=owner,
        )

    recover.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_and_reconnect_use_full_browser_lifecycle_timeout() -> None:
    start = AsyncMock(
        return_value=SimpleNamespace(
            session_id="session-1",
            profile=SimpleNamespace(cdp_port=9222),
        )
    )
    reconnect = AsyncMock(
        return_value=SimpleNamespace(
            session_id="session-2",
            profile=SimpleNamespace(cdp_port=9222),
        )
    )
    client = BrowserAgentClient(BrowserAgentConfig())
    client._browser_stub = SimpleNamespace(
        StartBrowser=start,
        ReconnectBrowser=reconnect,
    )

    await client.start_browser()
    await client.reconnect_browser()

    assert start.await_args.kwargs["timeout"] == 120.0
    assert reconnect.await_args.kwargs["timeout"] == 120.0


@pytest.mark.asyncio
async def test_list_campaigns_uses_exact_session_and_never_false_empties_errors() -> None:
    list_campaigns = AsyncMock(
        return_value=SimpleNamespace(
            campaigns=[SimpleNamespace(id="campaign-1", name="Campaign 1")],
        )
    )
    client = BrowserAgentClient(BrowserAgentConfig())
    client._session_id = "session-exact"
    client._scanner_stub = SimpleNamespace(ListCampaigns=list_campaigns)

    campaigns = await client.list_campaigns(ad_account_id="act_123")

    assert campaigns == [{"id": "campaign-1", "name": "Campaign 1"}]
    request = list_campaigns.await_args.args[0]
    assert request.session_id == "session-exact"
    assert request.ad_account_id == "123"

    list_campaigns.side_effect = RuntimeError("session unavailable")
    with pytest.raises(RuntimeError, match="session unavailable"):
        await client.list_campaigns(ad_account_id="123")


@pytest.mark.asyncio
async def test_list_campaigns_without_channel_is_unavailable_not_empty() -> None:
    client = BrowserAgentClient(BrowserAgentConfig())

    with pytest.raises(RuntimeError, match="channel is not initialized"):
        await client.list_campaigns(ad_account_id="123")

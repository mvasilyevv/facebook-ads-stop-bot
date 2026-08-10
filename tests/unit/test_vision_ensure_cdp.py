# -*- coding: utf-8 -*-
"""Unit-тесты fail-closed контракта POST /vision/ensure-cdp для platform healer.

Контракт: эндпоинт всегда возвращает 200 с {ok,status,action,message} и НЕ падает 5xx,
даже если browser-agent недоступен.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import apps.api.routers.v1.settings_vision as m


@pytest.fixture(autouse=True)
def configured_vision_runtime(monkeypatch):
    async def fake_load(_engine):
        return SimpleNamespace(profile_id="profile-exact")

    monkeypatch.setattr(m, "load_vision_runtime_config", fake_load)

    class FakeMaintenanceGuard:
        def __init__(self, _engine, owner):
            assert owner == "a" * 32

        async def __aenter__(self):
            return self

        async def assert_held(self):
            return None

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(m, "BrowserMaintenanceGuard", FakeMaintenanceGuard)


def _request():
    return SimpleNamespace(headers={"X-FB-Agent-Browser-Maintenance-Owner": "a" * 32})


@pytest.mark.asyncio
async def test_settings_probe_adopts_exact_maintenance_owner(monkeypatch):
    class UnexpectedSharedFence:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("maintenance-owned probe must not claim a shared fence")

    async def fake_probe(_client, *, expected_profile_id):
        assert expected_profile_id == "profile-exact"
        return m._BrowserChannelProbe("READY", None, 1, True)

    monkeypatch.setattr(m, "BrowserOperationFence", UnexpectedSharedFence)
    monkeypatch.setattr(m, "_probe_browser_channel", fake_probe)

    probe = await m._fenced_settings_probe(
        None,
        None,
        expected_profile_id="profile-exact",
        maintenance_owner="a" * 32,
    )

    assert probe.status == "READY"


@pytest.mark.asyncio
async def test_ensure_cdp_rejects_missing_platform_owner(monkeypatch):
    from core.tasks.browser_fence import BrowserMaintenanceGuard

    monkeypatch.setattr(m, "BrowserMaintenanceGuard", BrowserMaintenanceGuard)
    resp = await m.post_vision_ensure_cdp(
        request=SimpleNamespace(headers={}),
        engine=None,
        settings=None,
        meta_api_client=None,
    )

    assert resp.ok is False
    assert resp.status == "UNAVAILABLE"
    assert resp.action == "none"
    assert resp.message == "Platform maintenance ownership is missing or expired"


# Direct browser channel ready → no profile restart.
@pytest.mark.asyncio
async def test_ensure_cdp_already_ready(monkeypatch):
    called = {"restart": False}

    async def fake_probe(_client, *, expected_profile_id):
        assert expected_profile_id == "profile-exact"
        return m._BrowserChannelProbe("READY", None, 1, True)

    async def fake_recover(engine, settings, *, maintenance_owner):
        called["restart"] = True

    monkeypatch.setattr(m, "_probe_browser_channel", fake_probe)
    monkeypatch.setattr(m, "_recover_browser_profile_under_maintenance", fake_recover)

    resp = await m.post_vision_ensure_cdp(
        request=_request(),
        engine=None,
        settings=None,
        meta_api_client=None,
    )
    assert resp.ok is True
    assert resp.status == "READY"
    assert resp.action == "none"
    assert called["restart"] is False


# Channel degraded, exclusive profile restart successful and then directly confirmed.
@pytest.mark.asyncio
async def test_ensure_cdp_profile_recovery_success(monkeypatch):
    calls = {"n": 0}

    async def fake_probe(_client, *, expected_profile_id):
        assert expected_profile_id == "profile-exact"
        calls["n"] += 1
        return m._BrowserChannelProbe(
            "READY" if calls["n"] > 1 else "DEGRADED",
            None if calls["n"] > 1 else "probe_network_down",
            1,
            True,
            maintenance_recovery_allowed=calls["n"] == 1,
        )

    async def fake_recover(engine, settings, *, maintenance_owner):
        assert maintenance_owner == "a" * 32
        return None

    monkeypatch.setattr(m, "_probe_browser_channel", fake_probe)
    monkeypatch.setattr(m, "_recover_browser_profile_under_maintenance", fake_recover)

    resp = await m.post_vision_ensure_cdp(
        request=_request(),
        engine=None,
        settings=None,
        meta_api_client=None,
    )
    assert resp.ok is True
    assert resp.status == "RECOVERED"
    assert resp.action == "restart"


# Channel unavailable and recovery raises → ok=false without leaking details.
@pytest.mark.asyncio
async def test_ensure_cdp_agent_down_graceful(monkeypatch):
    async def fake_probe(_client, *, expected_profile_id):
        assert expected_profile_id == "profile-exact"
        return m._BrowserChannelProbe(
            "UNAVAILABLE",
            "probe_network_down",
            1,
            True,
            maintenance_recovery_allowed=True,
        )

    async def fake_recover(engine, settings, *, maintenance_owner):
        raise RuntimeError("gRPC browser-agent недоступен")

    monkeypatch.setattr(m, "_probe_browser_channel", fake_probe)
    monkeypatch.setattr(m, "_recover_browser_profile_under_maintenance", fake_recover)

    # Не должно бросать: platform healer получит явный fail-closed ok=false.
    resp = await m.post_vision_ensure_cdp(
        request=_request(),
        engine=None,
        settings=None,
        meta_api_client=None,
    )
    assert resp.ok is False
    assert resp.status == "UNAVAILABLE"
    assert resp.action == "restart"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "contract_compatible"),
    [
        ("login_required", True),
        ("meta_error: rate limit", True),
        ("token_not_found", True),
        ("browser-agent contract is incompatible", False),
    ],
)
async def test_ensure_cdp_never_restarts_non_recoverable_states(
    monkeypatch,
    message,
    contract_compatible,
):
    async def fake_probe(_client, *, expected_profile_id):
        return m._BrowserChannelProbe(
            "DEGRADED",
            message,
            1,
            contract_compatible,
            "session-1",
            "profile-exact",
            True,
            False,
            False,
        )

    recover = AsyncMock()
    monkeypatch.setattr(m, "_probe_browser_channel", fake_probe)
    monkeypatch.setattr(m, "_recover_browser_profile_under_maintenance", recover)

    resp = await m.post_vision_ensure_cdp(
        request=_request(),
        engine=None,
        settings=None,
        meta_api_client=None,
    )

    assert resp.ok is False
    assert resp.action == "none"
    recover.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_cdp_missing_db_config_is_fail_closed(monkeypatch):
    async def missing_config(_engine):
        raise m.VisionConfigurationError("missing")

    async def unexpected_probe(_client, *, expected_profile_id):
        raise AssertionError("unconfigured runtime must not probe browser-agent")

    monkeypatch.setattr(m, "load_vision_runtime_config", missing_config)
    monkeypatch.setattr(m, "_probe_browser_channel", unexpected_probe)

    resp = await m.post_vision_ensure_cdp(
        request=_request(),
        engine=None,
        settings=None,
        meta_api_client=None,
    )

    assert resp.ok is False
    assert resp.status == "UNAVAILABLE"
    assert resp.action == "none"
    assert resp.message == "Vision is not configured in PostgreSQL"

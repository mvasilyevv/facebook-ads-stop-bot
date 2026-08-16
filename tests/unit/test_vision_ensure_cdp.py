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
            # Зеркалим BrowserMaintenanceGuard.__post_init__: owner нормализуется
            # (.strip().lower()) ещё до того, как guard.owner кто-то прочитает.
            self.owner = owner.strip().lower()

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


class _FakeExclusiveMaintenance:
    """Фенс, который платформа берёт сама, когда владельца ей не передали."""

    instances: list[str] = []

    def __init__(self, _engine, *, operation_kind):
        self.operation_kind = operation_kind
        self.owner = "b" * 32

    async def __aenter__(self):
        _FakeExclusiveMaintenance.instances.append(self.operation_kind)
        return self

    async def assert_held(self):
        return None

    async def __aexit__(self, *_args):
        return False


@pytest.mark.asyncio
async def test_ensure_cdp_claims_its_own_fence_without_a_caller_owner(monkeypatch):
    """Деплою неоткуда взять владельца фенса: он его и не должен искать.

    Эндпоинт задуман для platform healer, а healer — это fbctl между стартом
    стола и проверкой канала. Требование готового владельца делало ручку
    невызываемой, поэтому без заголовка она берёт эксклюзив сама.
    """
    _FakeExclusiveMaintenance.instances.clear()
    recovered = {"called": False, "owner": ""}

    async def fake_probe(_client, *, expected_profile_id):
        assert expected_profile_id == "profile-exact"
        if recovered["called"]:
            return m._BrowserChannelProbe("READY", None, 1, True)
        return m._BrowserChannelProbe(
            "DEGRADED",
            "BROWSER_UNAVAILABLE",
            1,
            True,
            maintenance_recovery_allowed=True,
        )

    async def fake_recover(_engine, _settings, *, maintenance_owner):
        recovered["called"] = True
        recovered["owner"] = maintenance_owner

    monkeypatch.setattr(m, "BrowserExclusiveMaintenance", _FakeExclusiveMaintenance)
    monkeypatch.setattr(m, "_probe_browser_channel", fake_probe)
    monkeypatch.setattr(m, "_recover_browser_profile_under_maintenance", fake_recover)

    resp = await m.post_vision_ensure_cdp(
        request=SimpleNamespace(headers={}),
        engine=None,
        settings=None,
        meta_api_client=None,
    )

    assert resp.ok is True
    assert resp.status == "RECOVERED"
    assert recovered["called"] is True
    # Восстановление идёт под тем владельцем, которого выдал захваченный фенс.
    assert recovered["owner"] == "b" * 32
    assert _FakeExclusiveMaintenance.instances == ["vision_ensure_cdp"]


@pytest.mark.asyncio
async def test_ensure_cdp_still_adopts_an_explicit_owner(monkeypatch):
    """Вызов с готовым владельцем не должен захватывать второй фенс."""

    class UnexpectedExclusive:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("владелец передан — второй фенс брать нельзя")

    async def fake_probe(_client, *, expected_profile_id):
        return m._BrowserChannelProbe("READY", None, 1, True)

    monkeypatch.setattr(m, "BrowserExclusiveMaintenance", UnexpectedExclusive)
    monkeypatch.setattr(m, "_probe_browser_channel", fake_probe)

    resp = await m.post_vision_ensure_cdp(
        request=_request(),
        engine=None,
        settings=None,
        meta_api_client=None,
    )

    assert resp.ok is True
    assert resp.status == "READY"


# Занятый/недренированный/потерянный self-fence: ручка обязана вернуть
# 200-совместимое тело (не бросить) и назвать РЕАЛЬНУЮ причину, а не общее
# "owner rejected" — иначе healer примет активное чужое обслуживание за
# протухшего владельца и полезет чинить владельца вместо того, чтобы
# подождать (ревью Task 1, Important 2 + Important 3).
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc_type", "expected_message"),
    [
        (
            m.BrowserOperationBlocked,
            "Vision maintenance is active; CDP channel was not verified",
        ),
        (
            m.BrowserOperationDrainTimeout,
            "Active browser work did not drain; CDP channel was not verified",
        ),
        (
            m.BrowserFenceLeaseLost,
            "Vision maintenance fence was lost; state requires reconciliation",
        ),
    ],
    ids=["blocked", "drain_timeout", "lease_lost"],
)
async def test_ensure_cdp_reports_busy_self_fence_without_raising(
    monkeypatch,
    exc_type,
    expected_message,
):
    class RaisingExclusiveMaintenance:
        """Self-fence (без owner в заголовке), который не удаётся взять/удержать."""

        def __init__(self, _engine, *, operation_kind):
            assert operation_kind == "vision_ensure_cdp"

        async def __aenter__(self):
            raise exc_type("boom")

        async def assert_held(self):  # pragma: no cover - не должен вызываться
            return None

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(m, "BrowserExclusiveMaintenance", RaisingExclusiveMaintenance)
    recover = AsyncMock()
    monkeypatch.setattr(m, "_recover_browser_profile_under_maintenance", recover)

    resp = await m.post_vision_ensure_cdp(
        request=SimpleNamespace(headers={}),
        engine=None,
        settings=None,
        meta_api_client=None,
    )

    assert resp.ok is False
    assert resp.status == "UNAVAILABLE"
    assert resp.action == "none"
    assert resp.message == expected_message
    recover.assert_not_awaited()


# Explicit-owner путь: невалидный/просроченный owner — единственный случай,
# где формулировка про "ownership" правдива (Important 2).
@pytest.mark.asyncio
async def test_ensure_cdp_reports_invalid_supplied_owner_without_raising(monkeypatch):
    class RaisingMaintenanceGuard:
        def __init__(self, _engine, owner):
            assert owner == "a" * 32

        async def __aenter__(self):
            raise m.BrowserMaintenanceOwnerInvalid("browser maintenance owner is not active")

        async def assert_held(self):  # pragma: no cover - не должен вызываться
            return None

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(m, "BrowserMaintenanceGuard", RaisingMaintenanceGuard)

    resp = await m.post_vision_ensure_cdp(
        request=_request(),
        engine=None,
        settings=None,
        meta_api_client=None,
    )

    assert resp.ok is False
    assert resp.status == "UNAVAILABLE"
    assert resp.action == "none"
    assert resp.message == "Platform maintenance ownership is missing or expired"


# Minor 1: guard.owner уже нормализован (.strip().lower()) — сырой заголовок
# в другом регистре не должен уезжать в browser-agent как maintenance_owner.
@pytest.mark.asyncio
async def test_ensure_cdp_recovers_with_normalized_guard_owner_not_raw_header(monkeypatch):
    class UppercaseAwareGuard:
        def __init__(self, _engine, owner):
            assert owner == "A" * 32  # сырой заголовок ещё не нормализован
            self.owner = owner.strip().lower()

        async def __aenter__(self):
            return self

        async def assert_held(self):
            return None

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(m, "BrowserMaintenanceGuard", UppercaseAwareGuard)

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

    recovered = {"owner": None}

    async def fake_recover(_engine, _settings, *, maintenance_owner):
        recovered["owner"] = maintenance_owner

    monkeypatch.setattr(m, "_probe_browser_channel", fake_probe)
    monkeypatch.setattr(m, "_recover_browser_profile_under_maintenance", fake_recover)

    resp = await m.post_vision_ensure_cdp(
        request=SimpleNamespace(
            headers={"X-FB-Agent-Browser-Maintenance-Owner": "A" * 32},
        ),
        engine=None,
        settings=None,
        meta_api_client=None,
    )

    assert resp.ok is True
    assert resp.status == "RECOVERED"
    # Не "A" * 32 (сырой заголовок) — нормализованное guard.owner.
    assert recovered["owner"] == "a" * 32

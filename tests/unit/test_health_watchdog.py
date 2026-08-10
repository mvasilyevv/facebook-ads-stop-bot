# -*- coding: utf-8 -*-
"""Focused tests for the PostgreSQL-backed health watchdog."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import apps.health_watchdog.main as hw


@pytest.fixture(autouse=True)
def durable_browser_fence(monkeypatch):
    class FakeBrowserOperationFence:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def assert_held(self):
            return None

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(hw, "BrowserOperationFence", FakeBrowserOperationFence)
    monkeypatch.setattr(
        hw,
        "_load_canonical_vision_profile_id",
        AsyncMock(return_value="vision-profile-1"),
    )


@pytest.mark.parametrize(
    ("probe", "is_down", "reason"),
    [
        (
            {
                "healthy": True,
                "probe_performed": True,
                "probe_ok": True,
                "probe_detail": "ok",
                "browser_contract_version": hw.BROWSER_CONTRACT_VERSION,
                "vision_profile_id": "vision-profile-1",
            },
            False,
            "ok",
        ),
        (
            {
                "healthy": False,
                "probe_performed": True,
                "probe_detail": "probe_network_down",
            },
            True,
            "probe_network_down",
        ),
        (
            {
                "healthy": False,
                "probe_performed": False,
                "detail": "circuit_open: browser-agent unavailable",
            },
            True,
            "circuit_open: browser-agent unavailable",
        ),
    ],
)
def test_classify_meta_probe(
    probe: dict[str, object],
    is_down: bool,
    reason: str,
) -> None:
    assert hw.classify_meta_probe(
        probe,
        expected_profile_id="vision-profile-1",
    ) == (is_down, reason)


def test_classify_meta_probe_rejects_stale_contract_and_foreign_profile() -> None:
    healthy = {
        "healthy": True,
        "probe_performed": True,
        "probe_ok": True,
        "browser_contract_version": hw.BROWSER_CONTRACT_VERSION,
        "vision_profile_id": "vision-profile-1",
    }

    assert (
        hw.classify_meta_probe(
            healthy | {"browser_contract_version": hw.BROWSER_CONTRACT_VERSION - 1},
            expected_profile_id="vision-profile-1",
        )[0]
        is True
    )
    assert hw.classify_meta_probe(
        healthy | {"vision_profile_id": "other-profile"},
        expected_profile_id="vision-profile-1",
    ) == (True, "vision_profile_mismatch")


@pytest.mark.parametrize(
    "probe_patch",
    [
        {"probe_performed": False, "probe_ok": False, "probe_detail": "not_performed"},
        {"probe_performed": True, "probe_ok": False, "probe_detail": "meta_error:17"},
    ],
)
def test_classify_meta_probe_never_resolves_on_unconfirmed_full_probe(
    probe_patch: dict[str, object],
) -> None:
    probe = {
        "healthy": True,
        "browser_contract_version": hw.BROWSER_CONTRACT_VERSION,
        "vision_profile_id": "vision-profile-1",
        **probe_patch,
    }

    is_down, reason = hw.classify_meta_probe(
        probe,
        expected_profile_id="vision-profile-1",
    )

    assert is_down is True
    assert reason.startswith("full_probe_unconfirmed:")


def test_is_login_required_reason() -> None:
    assert hw.is_login_required_reason("LOGIN_REQUIRED") is True
    assert hw.is_login_required_reason("probe_network_down") is False


async def test_check_meta_api_channel_skips_when_scanning_off(monkeypatch) -> None:
    from core.observer import queries as observer_queries

    monkeypatch.setattr(
        observer_queries,
        "load_observer_config",
        AsyncMock(return_value={"is_scanning_enabled": False}),
    )
    client = SimpleNamespace(check_health=AsyncMock(return_value={"healthy": False}))
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(hw, "_enqueue_critical_notification", notify)

    sent = await hw.check_meta_api_channel(
        client,
        engine=object(),
    )

    assert sent is False
    client.check_health.assert_not_awaited()
    notify.assert_not_awaited()


async def test_check_meta_api_channel_alerts_when_scanning_on_and_down(monkeypatch) -> None:
    from core.observer import queries as observer_queries

    monkeypatch.setattr(
        observer_queries,
        "load_observer_config",
        AsyncMock(return_value={"is_scanning_enabled": True}),
    )
    client = SimpleNamespace(
        check_health=AsyncMock(return_value={"healthy": False, "detail": "network-down"})
    )
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(hw, "_enqueue_critical_notification", notify)

    sent = await hw.check_meta_api_channel(client, engine=object())

    assert sent is True
    client.check_health.assert_awaited_once_with(
        full_probe=True,
        expected_profile_id="vision-profile-1",
    )
    assert notify.await_args.kwargs["event_type"] == "meta_channel_unavailable"


async def test_meta_probe_reads_canonical_profile_inside_shared_fence(monkeypatch) -> None:
    from core.observer import queries as observer_queries

    events: list[str] = []

    class OrderedFence:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            events.append("fence_enter")
            return self

        async def assert_held(self):
            events.append("fence_assert")

        async def __aexit__(self, *_args):
            events.append("fence_exit")
            return False

    async def load_profile(_engine):
        events.append("profile_read")
        return "vision-profile-b"

    async def check_health(**kwargs):
        events.append("probe")
        assert kwargs["expected_profile_id"] == "vision-profile-b"
        return {
            "healthy": True,
            "probe_performed": True,
            "probe_ok": True,
            "probe_detail": "ok",
            "browser_contract_version": hw.BROWSER_CONTRACT_VERSION,
            "vision_profile_id": "vision-profile-b",
        }

    monkeypatch.setattr(
        observer_queries,
        "load_observer_config",
        AsyncMock(return_value={"is_scanning_enabled": True}),
    )
    monkeypatch.setattr(hw, "BrowserOperationFence", OrderedFence)
    monkeypatch.setattr(hw, "_load_canonical_vision_profile_id", load_profile)
    monkeypatch.setattr(hw, "_resolve_critical_notification", AsyncMock(return_value=True))

    assert (
        await hw.check_meta_api_channel(
            SimpleNamespace(check_health=check_health),
            engine=object(),
        )
        is False
    )
    assert events == [
        "fence_enter",
        "profile_read",
        "probe",
        "fence_assert",
        "fence_exit",
    ]


class _Result:
    def __init__(self, *, actor_count: int, all_fresh: bool) -> None:
        self._row = SimpleNamespace(actor_count=actor_count, all_fresh=all_fresh)

    def one(self):
        return self._row


class _Connection:
    def __init__(self, result: _Result) -> None:
        self.result = result
        self.params: dict[str, object] | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _statement, params):
        self.params = params
        return self.result


class _Engine:
    def __init__(self, *, actor_count: int, all_fresh: bool) -> None:
        self.connection = _Connection(_Result(actor_count=actor_count, all_fresh=all_fresh))

    def connect(self):
        return self.connection


@pytest.mark.parametrize(
    ("actor_count", "all_fresh", "expected"),
    [(1, True, True), (1, False, False), (0, False, False)],
)
async def test_reported_side_liveness_uses_cabinet_runtime(
    monkeypatch,
    actor_count: int,
    all_fresh: bool,
    expected: bool,
) -> None:
    from core.observer import queries as observer_queries

    monkeypatch.setattr(
        observer_queries,
        "load_observer_config",
        AsyncMock(return_value={"is_scanning_enabled": True}),
    )
    now = datetime(2026, 7, 3, 8, 40, tzinfo=timezone.utc)
    engine = _Engine(actor_count=actor_count, all_fresh=all_fresh)

    assert (
        await hw._is_reported_side_live(
            engine,
            account_ids=["act_111", "111", "222"],
            now=now,
        )
        is expected
    )
    assert engine.connection.params == {
        "account_ids": ["111", "222"],
        "fresh_after": now - timedelta(seconds=hw.REPORTED_SNAPSHOT_MAX_AGE_SECONDS),
    }


class _ShadowDecisionRecorder:
    """Small unit double for the separately integration-tested PostgreSQL state."""

    def __init__(self) -> None:
        self.samples: list[hw.ShadowSample] = []

    async def __call__(self, _engine, *, account_id, sample, cabinet_day_start):
        del account_id, cabinet_day_start
        previous = max(self.samples, key=lambda item: item.ts, default=None)
        self.samples.append(sample)
        verdict = hw.detect_shadow(
            self.samples,
            window_seconds=hw.SHADOW_WINDOW_SECONDS,
            billing_min_delta_minor=hw.SHADOW_BILLING_MIN_DELTA_MINOR,
            reported_max_delta_minor=hw.SHADOW_REPORTED_MAX_DELTA_MINOR,
        )
        return hw.ShadowObservationDecision(
            previous_sample=previous,
            verdict=verdict,
            recovery_confirmed=False,
            incident_event_committed=verdict is not None,
        )


class _ShadowMetaClient:
    def __init__(self, amount_spent: str) -> None:
        self.amount_spent = amount_spent
        self.called = False

    async def execute_graph_call(self, **_kwargs):
        self.called = True
        return {"amount_spent": self.amount_spent}


async def test_shadow_tick_uses_postgres_clock_when_now_is_not_injected(monkeypatch) -> None:
    from core.meta_api import account_tz

    db_now = datetime(2026, 7, 3, 8, 40, tzinfo=timezone.utc)
    clock = AsyncMock(return_value=db_now)
    monkeypatch.setattr(hw, "_database_now", clock)
    monkeypatch.setattr(account_tz, "active_account_ids", AsyncMock(return_value=[]))
    engine = object()

    assert await hw.check_shadow_spend(_ShadowMetaClient("1000"), engine=engine) is False
    clock.assert_awaited_once_with(engine)


def _configure_shadow(monkeypatch, *, reported: str = "5.00") -> _ShadowDecisionRecorder:
    from decimal import Decimal

    from core.dashboard import cabinet_spend
    from core.meta_api import account_tz

    monkeypatch.setattr(hw, "_is_reported_side_live", AsyncMock(return_value=True))
    monkeypatch.setattr(
        account_tz,
        "active_account_ids",
        AsyncMock(return_value=["111222"]),
    )
    monkeypatch.setattr(
        account_tz,
        "resolve_cabinet_days",
        AsyncMock(
            return_value=SimpleNamespace(
                missing_account_ids=(),
                query_boundaries={"111222": datetime(2026, 7, 3, 0, 0, tzinfo=timezone.utc)},
            )
        ),
    )
    monkeypatch.setattr(
        account_tz,
        "resolve_account_currencies",
        AsyncMock(
            return_value=SimpleNamespace(
                currencies={"111222": "USD"},
            )
        ),
    )
    monkeypatch.setattr(
        hw,
        "resolve_recurring_incident",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        cabinet_spend,
        "current_day_spend_for_account",
        AsyncMock(return_value=Decimal(reported)),
    )
    recorder = _ShadowDecisionRecorder()
    monkeypatch.setattr(hw, "_record_shadow_observation", recorder)
    return recorder


async def test_shadow_spend_skips_when_snapshot_is_not_fresh(monkeypatch) -> None:
    from core.meta_api import account_tz

    monkeypatch.setattr(hw, "_is_reported_side_live", AsyncMock(return_value=False))
    monkeypatch.setattr(
        account_tz,
        "active_account_ids",
        AsyncMock(return_value=["111222"]),
    )
    client = _ShadowMetaClient("1030")
    resolve = AsyncMock(return_value=True)
    monkeypatch.setattr(hw, "_resolve_critical_notification", resolve)

    sent = await hw.check_shadow_spend(
        client,
        engine=object(),
        now=datetime(2026, 7, 3, 8, 40, tzinfo=timezone.utc),
    )

    assert sent is False
    assert client.called is False
    resolve.assert_not_awaited()


async def test_shadow_spend_hides_money_and_opens_incident_without_currency(
    monkeypatch,
) -> None:
    from core.meta_api import account_tz

    monkeypatch.setattr(hw, "_is_reported_side_live", AsyncMock(return_value=True))
    monkeypatch.setattr(
        account_tz,
        "active_account_ids",
        AsyncMock(return_value=["111222"]),
    )
    monkeypatch.setattr(
        account_tz,
        "resolve_cabinet_days",
        AsyncMock(
            return_value=SimpleNamespace(
                missing_account_ids=(),
                query_boundaries={"111222": datetime(2026, 7, 3, 0, 0, tzinfo=timezone.utc)},
            )
        ),
    )
    monkeypatch.setattr(
        account_tz,
        "resolve_account_currencies",
        AsyncMock(return_value=SimpleNamespace(currencies={})),
    )
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(hw, "notify_recurring_incident", notify)
    client = _ShadowMetaClient("1030")

    alerted = await hw.check_shadow_spend(
        client,
        engine=object(),
        now=datetime(2026, 7, 3, 8, 40, tzinfo=timezone.utc),
    )

    assert alerted is True
    assert client.called is False
    notify.assert_awaited_once()
    assert "валюта" in notify.await_args.kwargs["summary"].lower()
    assert notify.await_args.kwargs["lines"] == [
        "Денежные значения скрыты; сравнение не выполняется"
    ]


async def test_shadow_spend_alerts_and_enqueues_one_durable_scan(monkeypatch) -> None:
    recorder = _configure_shadow(monkeypatch)
    enqueue_scan = AsyncMock()
    monkeypatch.setattr(hw, "enqueue_observer_scan", enqueue_scan)
    engine = object()
    t0 = datetime(2026, 7, 3, 8, 40, tzinfo=timezone.utc)

    assert await hw.check_shadow_spend(_ShadowMetaClient("1000"), engine=engine, now=t0) is False
    sent = await hw.check_shadow_spend(
        _ShadowMetaClient("1030"),
        engine=engine,
        now=t0 + timedelta(seconds=300),
    )

    assert sent is True
    enqueue_scan.assert_awaited_once()
    assert enqueue_scan.await_args.kwargs["requested_by"] == "health_watchdog"
    assert enqueue_scan.await_args.kwargs["reason"] == "shadow_spend:act_111222"
    verdict = hw.detect_shadow(recorder.samples, window_seconds=hw.SHADOW_WINDOW_SECONDS)
    assert verdict is not None
    assert verdict.billing_delta_minor == 30


async def test_one_cent_shadow_movement_wakes_scan_without_alert(monkeypatch) -> None:
    _configure_shadow(monkeypatch)
    enqueue_scan = AsyncMock()
    monkeypatch.setattr(hw, "enqueue_observer_scan", enqueue_scan)
    engine = object()
    t0 = datetime(2026, 7, 3, 8, 40, tzinfo=timezone.utc)

    await hw.check_shadow_spend(_ShadowMetaClient("1000"), engine=engine, now=t0)
    sent = await hw.check_shadow_spend(
        _ShadowMetaClient("1001"),
        engine=engine,
        now=t0 + timedelta(seconds=30),
    )

    assert sent is False
    enqueue_scan.assert_awaited_once()


def test_shadow_recovery_requires_reported_spend_to_catch_up() -> None:
    baseline = hw.ShadowSample(
        ts=datetime(2026, 7, 3, 8, 40, tzinfo=timezone.utc),
        currency="USD",
        billing_minor=1000,
        reported_minor=500,
    )
    frozen = hw.ShadowSample(
        ts=baseline.ts + timedelta(minutes=1),
        currency="USD",
        billing_minor=1030,
        reported_minor=500,
    )
    caught_up = hw.ShadowSample(
        ts=baseline.ts + timedelta(minutes=2),
        currency="USD",
        billing_minor=1030,
        reported_minor=530,
    )

    assert hw._shadow_reporting_caught_up(baseline, frozen) is False
    assert hw._shadow_reporting_caught_up(baseline, caught_up) is True

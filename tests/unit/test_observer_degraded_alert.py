# -*- coding: utf-8 -*-
"""Observer degraded incident: durable PostgreSQL notification path."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import apps.observer_worker.main as ow


# Durable outbox accepted the event.
@pytest.mark.asyncio
async def test_degraded_alert_delivers_via_recipients(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(ow, "notify_recurring_incident", spy)

    ok = await ow._maybe_alert_degraded(
        object(),
        consecutive_failures=44,
        last_error="AioRpcError: профиль не запущен",
    )

    assert ok is True
    spy.assert_awaited_once()
    facts = spy.await_args.kwargs
    assert facts["severity"] == "critical"
    # Заголовок по-русски и без имени компонента, в сводке — сколько раз подряд.
    assert "отсканировать кабинет" in facts["title"]
    assert "Observer" not in facts["title"]
    assert "44 раза" in facts["summary"]
    assert any("вручную" in line.lower() for line in facts["lines"])
    assert facts["incident_key"] == ow.OBSERVER_DEGRADED_INCIDENT_KEY
    assert facts["audience"] == "all"
    assert facts["resource_type"] == "worker"


# Every detected tick reaches the durable facade with the same event key.
@pytest.mark.asyncio
async def test_degraded_alert_repeated_ticks_use_same_event_key(monkeypatch):
    spy = AsyncMock(side_effect=(True, False))
    monkeypatch.setattr(ow, "notify_recurring_incident", spy)

    first = await ow._maybe_alert_degraded(object(), consecutive_failures=5, last_error=None)
    second = await ow._maybe_alert_degraded(object(), consecutive_failures=6, last_error=None)

    assert first is True
    assert second is False
    assert spy.await_count == 2
    assert {call.kwargs["incident_key"] for call in spy.await_args_list} == {
        ow.OBSERVER_DEGRADED_INCIDENT_KEY
    }


# Outbox rejection is visible in logs.
@pytest.mark.asyncio
async def test_degraded_alert_outbox_rejection_warns(monkeypatch, caplog):
    spy = AsyncMock(return_value=False)
    monkeypatch.setattr(ow, "notify_recurring_incident", spy)

    with caplog.at_level("WARNING"):
        ok = await ow._maybe_alert_degraded(object(), consecutive_failures=7, last_error="net down")

    assert ok is False
    spy.assert_awaited_once()
    assert any("outbox" in r.getMessage().lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_degraded_lifecycle_never_resolves_partial_or_unknown(monkeypatch) -> None:
    state = ow._ObserverState(consecutive_scan_failures=2)
    alert = AsyncMock(return_value=True)
    resolve = AsyncMock(return_value=True)
    monkeypatch.setattr(ow, "_maybe_alert_degraded", alert)
    monkeypatch.setattr(ow, "resolve_recurring_incident", resolve)
    monkeypatch.setattr(ow, "DEGRADED_ALERT_THRESHOLD", 3)

    await ow._track_degraded_incident(
        object(),
        state=state,
        summary={"outcome": "partial", "error": "missing rows"},
    )
    await ow._track_degraded_incident(
        object(),
        state=state,
        summary={"outcome": "unknown"},
    )

    assert state.consecutive_scan_failures == 3
    alert.assert_awaited_once()
    resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_degraded_lifecycle_resolves_only_confirmed_complete_scan(monkeypatch) -> None:
    state = ow._ObserverState(consecutive_scan_failures=4)
    resolve = AsyncMock(return_value=True)
    monkeypatch.setattr(ow, "resolve_recurring_incident", resolve)

    await ow._track_degraded_incident(
        object(),
        state=state,
        summary={"outcome": "success"},
    )

    assert state.consecutive_scan_failures == 0
    resolve.assert_awaited_once()
    assert resolve.await_args.kwargs["incident_key"] == ow.OBSERVER_DEGRADED_INCIDENT_KEY


@pytest.mark.asyncio
async def test_main_loop_counts_an_unhandled_claimed_scan_crash_once(monkeypatch) -> None:
    class _Engine:
        async def dispose(self) -> None:
            return None

    async def _metrics(*_args, **_kwargs) -> None:
        await asyncio.Event().wait()

    async def _crash(*_args, **_kwargs):
        raise RuntimeError("cycle crashed")

    continue_values = iter((True, False))
    degraded = AsyncMock(return_value=True)
    monkeypatch.setattr(ow, "start_worker_metrics_server", lambda *_args: None)
    monkeypatch.setattr(ow, "_get_database_url", lambda: "postgresql+asyncpg://unused")
    monkeypatch.setattr(ow, "create_async_engine", lambda *_args, **_kwargs: _Engine())
    monkeypatch.setattr(ow, "metrics_loop", _metrics)
    task = SimpleNamespace(id=1842, lease_owner=uuid.uuid4(), lease_token=3)
    monkeypatch.setattr(ow, "claim_observer_scan", AsyncMock(return_value=task))
    monkeypatch.setattr(ow, "_run_claimed_observer_scan", _crash)
    monkeypatch.setattr(ow, "_maybe_alert_degraded", degraded)
    monkeypatch.setattr(ow, "DEGRADED_ALERT_THRESHOLD", 1)
    monkeypatch.setattr(ow.asyncio, "sleep", AsyncMock())

    await ow.main_loop(
        gate_factory=AsyncMock(return_value=object()),
        should_continue=lambda: next(continue_values),
    )

    degraded.assert_awaited_once()
    assert degraded.await_args.kwargs["consecutive_failures"] == 1

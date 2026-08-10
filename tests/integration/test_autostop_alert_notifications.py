"""Autostop alerting uses persisted recurring incidents, never Redis gates."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import core.meta_api.autostop_alert as mod
from core.meta_api.autostop_alert import (
    AUTOSTOP_CHANNEL_INCIDENT_KEY,
    _confirmed_spend_text,
    escalate_undelivered_autostop_pauses,
    maybe_alert_autostop_channel_down,
)
from core.meta_api.errors import RateLimitedError, TemporaryError


@pytest.mark.asyncio
async def test_channel_down_reaches_recurring_incident_with_stable_key(monkeypatch) -> None:
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(mod, "notify_recurring_incident", notify)
    exc = TemporaryError("Failed to fetch", code=-2)

    first = await maybe_alert_autostop_channel_down(
        exc=exc,
        fb_ad_id="120246662749510044",
        engine=object(),
    )
    second = await maybe_alert_autostop_channel_down(
        exc=exc,
        fb_ad_id="120246662749510044",
        engine=object(),
    )

    assert first is True
    assert second is True
    assert notify.await_count == 2
    assert {call.kwargs["incident_key"] for call in notify.await_args_list} == {
        AUTOSTOP_CHANNEL_INCIDENT_KEY
    }
    assert {call.kwargs["audience"] for call in notify.await_args_list} == {"all"}
    assert {call.kwargs["resource_type"] for call in notify.await_args_list} == {"meta_channel"}


@pytest.mark.asyncio
async def test_channel_down_propagates_notifier_rejection(monkeypatch) -> None:
    notify = AsyncMock(return_value=False)
    monkeypatch.setattr(mod, "notify_recurring_incident", notify)

    accepted = await maybe_alert_autostop_channel_down(
        exc=TemporaryError("Failed to fetch", code=-2),
        fb_ad_id="AD_123",
        engine=object(),
    )

    assert accepted is False
    notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_rate_limit_does_not_create_channel_outage_event(monkeypatch) -> None:
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(mod, "notify_recurring_incident", notify)

    accepted = await maybe_alert_autostop_channel_down(
        exc=RateLimitedError("throttled", code=4),
        fb_ad_id="AD_123",
        engine=object(),
    )

    assert accepted is False
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_per_ad_scan_without_engine_is_silent() -> None:
    accepted = await escalate_undelivered_autostop_pauses(None)
    assert accepted == 0


def test_per_ad_spend_requires_exact_reviewed_currency_identity() -> None:
    assert _confirmed_spend_text("18.40", "USD") == "18.40 USD"
    assert _confirmed_spend_text("18", "JPY") == "18 JPY"
    assert _confirmed_spend_text("18.401", "KWD") == "18.401 KWD"
    assert _confirmed_spend_text("18.4", None) is None
    assert _confirmed_spend_text("18.4", "XAU") is None
    assert _confirmed_spend_text("18.4", "JPY") is None
    assert _confirmed_spend_text(None, "USD") is None

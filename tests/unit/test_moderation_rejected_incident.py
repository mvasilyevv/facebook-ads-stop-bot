from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.observer import writers


@pytest.mark.asyncio
async def test_rejected_ad_opens_one_named_incident_with_meta_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notify = AsyncMock()
    resolve = AsyncMock()
    monkeypatch.setattr(writers, "notify_recurring_incident_in_transaction", notify)
    monkeypatch.setattr(writers, "resolve_recurring_incident_in_transaction", resolve)

    await writers._sync_moderation_incident_in_transaction(
        AsyncMock(),
        fb_ad_id="120200000000001",
        ad_name="CR2 creative 7",
        delivery_status="DISAPPROVED",
        previous_delivery_status="ACTIVE",
        moderation_reason="  Personal attributes:  policy violation  ",
    )

    assert notify.await_count == 1
    facts = notify.await_args.kwargs
    assert facts["incident_key"] == "moderation-rejected:120200000000001"
    assert facts["title"] == "Объявление отклонено: CR2 creative 7"
    assert facts["summary"] == "Причина: Personal attributes: policy violation"
    assert facts["resource_id"] == "120200000000001"
    assert facts["lines"][-1] == ("Исправь объявление или запроси повторную проверку в Ads Manager")
    resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejected_ad_says_reason_is_unknown_without_inventing_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notify = AsyncMock()
    monkeypatch.setattr(writers, "notify_recurring_incident_in_transaction", notify)

    await writers._sync_moderation_incident_in_transaction(
        AsyncMock(),
        fb_ad_id="120200000000001",
        ad_name="CR2 creative 7",
        delivery_status="DISAPPROVED",
        previous_delivery_status="DISAPPROVED",
        moderation_reason=None,
    )

    assert notify.await_args.kwargs["incident_key"] == ("moderation-rejected:120200000000001")
    assert notify.await_args.kwargs["summary"] == (
        "Причина неизвестна: Facebook не передал её в данных скана."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("active_status", ["ACTIVE", "WITH_ISSUES"])
async def test_rejection_incident_resolves_when_ad_delivers_again(
    monkeypatch: pytest.MonkeyPatch,
    active_status: str,
) -> None:
    notify = AsyncMock()
    resolve = AsyncMock()
    monkeypatch.setattr(writers, "notify_recurring_incident_in_transaction", notify)
    monkeypatch.setattr(writers, "resolve_recurring_incident_in_transaction", resolve)

    await writers._sync_moderation_incident_in_transaction(
        AsyncMock(),
        fb_ad_id="120200000000001",
        ad_name="CR2 creative 7",
        delivery_status=active_status,
        previous_delivery_status="DISAPPROVED",
        moderation_reason=None,
    )

    notify.assert_not_awaited()
    assert resolve.await_args.kwargs == {
        "incident_key": "moderation-rejected:120200000000001",
        "audience": "owners",
        "summary": "Объявление CR2 creative 7 снова активно.",
    }


@pytest.mark.asyncio
async def test_paused_ad_does_not_close_rejection_incident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notify = AsyncMock()
    resolve = AsyncMock()
    monkeypatch.setattr(writers, "notify_recurring_incident_in_transaction", notify)
    monkeypatch.setattr(writers, "resolve_recurring_incident_in_transaction", resolve)

    await writers._sync_moderation_incident_in_transaction(
        AsyncMock(),
        fb_ad_id="120200000000001",
        ad_name="CR2 creative 7",
        delivery_status="CAMPAIGN_PAUSED",
        previous_delivery_status="DISAPPROVED",
        moderation_reason=None,
    )

    notify.assert_not_awaited()
    resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_active_ad_resolves_an_open_incident_after_intermediate_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve = AsyncMock()
    monkeypatch.setattr(writers, "resolve_recurring_incident_in_transaction", resolve)

    await writers._sync_moderation_incident_in_transaction(
        AsyncMock(),
        fb_ad_id="120200000000001",
        ad_name="CR2 creative 7",
        delivery_status="ACTIVE",
        previous_delivery_status="PENDING_REVIEW",
        moderation_reason=None,
        incident_was_open=True,
    )

    assert resolve.await_count == 1

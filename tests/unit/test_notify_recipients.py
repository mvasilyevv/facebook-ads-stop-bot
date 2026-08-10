from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

import core.telegram.worker_notify as worker_notify
from core.telegram.notifications import EnqueuedNotification

_SCHEDULED_AT = datetime(2026, 7, 22, tzinfo=UTC)


@pytest.mark.asyncio
async def test_recipients_notification_is_committed_to_outbox(monkeypatch) -> None:
    enqueue = AsyncMock(
        return_value=EnqueuedNotification(event_id=uuid.uuid4(), delivery_count=2, was_created=True)
    )
    monkeypatch.setattr(worker_notify, "enqueue_notification_in_rolling_window", enqueue)
    accepted = await worker_notify.notify_recipients(
        object(),
        event_type="watchdog_channel",
        severity="warning",
        title="Канал degraded",
        summary="Источник недоступен",
        dedupe_key="watchdog:channel",
        dedupe_ttl_seconds=300,
        scheduled_at=_SCHEDULED_AT,
    )

    assert accepted is True
    spec = enqueue.await_args.args[1]
    assert enqueue.await_args.kwargs["window_seconds"] == 300
    assert spec.audience == "all"
    assert spec.event_type == "worker_watchdog_channel"
    assert spec.severity == "warning"
    assert spec.facts.title == "Канал degraded"
    assert spec.facts.summary == "Источник недоступен"
    assert spec.scheduled_at == _SCHEDULED_AT


def test_windowed_dedupe_key_is_stable_without_epoch_bucket() -> None:
    facts = worker_notify.NotificationCardFacts(title="Same incident")

    first = worker_notify._dedupe_key(
        event_type="worker_watchdog_channel",
        facts=facts,
        dedupe_key="watchdog:channel",
        dedupe_ttl_seconds=300,
    )
    second = worker_notify._dedupe_key(
        event_type="worker_watchdog_channel",
        facts=facts,
        dedupe_key="watchdog:channel",
        dedupe_ttl_seconds=300,
    )

    assert first == second
    assert len(first.rsplit(":", 1)[-1]) == 24


@pytest.mark.asyncio
async def test_durable_duplicate_is_treated_as_already_accepted(monkeypatch) -> None:
    monkeypatch.setattr(
        worker_notify,
        "enqueue_notification",
        AsyncMock(
            return_value=EnqueuedNotification(
                event_id=uuid.uuid4(), delivery_count=0, was_created=False
            )
        ),
    )

    accepted = await worker_notify.notify_recipients(
        object(),
        event_type="watchdog_channel",
        severity="warning",
        title="Same",
        dedupe_key="stable",
        scheduled_at=_SCHEDULED_AT,
    )

    assert accepted is True


@pytest.mark.asyncio
async def test_new_event_without_recipient_delivery_returns_false(monkeypatch) -> None:
    monkeypatch.setattr(
        worker_notify,
        "enqueue_notification",
        AsyncMock(
            return_value=EnqueuedNotification(
                event_id=uuid.uuid4(), delivery_count=0, was_created=True
            )
        ),
    )

    accepted = await worker_notify.notify_recipients(
        object(),
        event_type="watchdog_channel",
        severity="warning",
        title="No recipients",
        scheduled_at=_SCHEDULED_AT,
    )

    assert accepted is False

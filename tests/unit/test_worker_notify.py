from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

import core.telegram.worker_notify as worker_notify
from core.telegram.notifications import EnqueuedNotification


@pytest.mark.asyncio
async def test_owner_notification_uses_durable_owner_audience(monkeypatch) -> None:
    enqueue = AsyncMock(
        return_value=EnqueuedNotification(event_id=uuid.uuid4(), delivery_count=1, was_created=True)
    )
    monkeypatch.setattr(worker_notify, "enqueue_notification", enqueue)
    accepted = await worker_notify.notify_owners(
        object(),
        event_type="money_action_failed",
        severity="critical",
        title="Отключение не подтверждено",
        summary="Нужна проверка",
        dedupe_key="money:task:7",
    )

    assert accepted is True
    spec = enqueue.await_args.args[1]
    assert spec.audience == "owners"
    assert spec.severity == "critical"
    assert spec.facts.title == "Отключение не подтверждено"
    assert spec.facts.summary == "Нужна проверка"


@pytest.mark.asyncio
async def test_typed_worker_card_cannot_mint_an_unverified_action(monkeypatch) -> None:
    enqueue = AsyncMock(
        return_value=EnqueuedNotification(event_id=uuid.uuid4(), delivery_count=1, was_created=True)
    )
    monkeypatch.setattr(worker_notify, "enqueue_notification", enqueue)

    accepted = await worker_notify.notify_owners(
        object(),
        event_type="draft",
        severity="warning",
        title="Preview",
        scheduled_at=datetime(2026, 7, 22, tzinfo=UTC),
    )

    assert accepted is True
    spec = enqueue.await_args.args[1]
    assert spec.actions == []


@pytest.mark.asyncio
async def test_outbox_failure_is_reported_without_direct_fallback(monkeypatch) -> None:
    enqueue = AsyncMock(side_effect=RuntimeError("postgres unavailable"))
    monkeypatch.setattr(worker_notify, "enqueue_notification", enqueue)

    accepted = await worker_notify.notify_owners(
        object(),
        event_type="money_action_failed",
        severity="critical",
        title="Failure",
    )

    assert accepted is False


@pytest.mark.asyncio
async def test_recurring_incident_rejects_noncritical_severity() -> None:
    with pytest.raises(ValueError, match="must be critical"):
        await worker_notify.notify_recurring_incident(
            object(),
            incident_key="worker:test",
            audience="owners",
            event_type="test_warning",
            severity="warning",
            title="Not critical",
        )

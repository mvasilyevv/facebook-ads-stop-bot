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
async def test_recurring_incident_rejects_non_operational_severity() -> None:
    with pytest.raises(ValueError, match="must be warning or critical"):

async def test_recurring_incident_accepts_warning_severity(monkeypatch) -> None:
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(worker_notify, "notify_recurring_incident_in_transaction", notify)

    class _Begin:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    class _Engine:
        def begin(self):
            return _Begin()

    accepted = await worker_notify.notify_recurring_incident(
        _Engine(),
        incident_key="worker:test",
        audience="owners",
        event_type="test_warning",
        severity="warning",
        title="Предупреждение",
    )

    assert accepted is True
    assert notify.await_args.kwargs["severity"] == "warning"


@pytest.mark.asyncio
async def test_recurring_incident_rejects_non_alert_severity() -> None:
    with pytest.raises(ValueError, match="warning or critical"):
        await worker_notify.notify_recurring_incident(
            object(),
            incident_key="worker:test",
            audience="owners",
            event_type="test_unknown",
            severity="unknown",
            title="Unknown",

            event_type="test_recovery",
            severity="ok",
            title="Не алерт",
        )


@pytest.mark.asyncio
async def test_recurring_incident_accepts_warning_severity(monkeypatch) -> None:
    persist = AsyncMock(return_value=True)
    monkeypatch.setattr(worker_notify, "notify_recurring_incident_in_transaction", persist)

    class BeginContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return None

    class Engine:
        def begin(self):
            return BeginContext()

    accepted = await worker_notify.notify_recurring_incident(
        Engine(),  # type: ignore[arg-type]
        incident_key="worker:warning",
        audience="owners",
        event_type="test_warning",
        severity="warning",
        title="Needs attention",
    )

    assert accepted is True
    assert persist.await_args.kwargs["severity"] == "warning"

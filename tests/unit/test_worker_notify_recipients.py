# -*- coding: utf-8 -*-
"""Task 4 Волна 2: worker-нотификации через recipients-путь (без forum-топиков).

Проверяем, что каждый воркер использует recipients-канал и НЕ использует forum_thread.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# =================== health_watchdog ===================


# health: every condition reaches the durable notifier; Redis is not consulted.
@pytest.mark.asyncio
async def test_health_with_engine_calls_recurring_incident(monkeypatch) -> None:
    import inspect

    import apps.health_watchdog.main as hw

    engine = MagicMock()
    spy_notify = AsyncMock(return_value=True)
    monkeypatch.setattr(hw, "notify_recurring_incident", spy_notify)

    ok = await hw._enqueue_critical_notification(
        incident_key="test:key",
        engine=engine,
        event_type="health_test",
        title="Тестовый алерт",
        summary="Источник недоступен",
        risk="Данные могут быть устаревшими",
    )
    assert ok is True
    spy_notify.assert_awaited_once()
    assert spy_notify.await_args.kwargs["event_type"] == "health_test"
    assert spy_notify.await_args.kwargs["severity"] == "critical"
    assert spy_notify.await_args.kwargs["incident_key"] == "test:key"
    assert spy_notify.await_args.kwargs["audience"] == "all"
    assert "redis_client" not in inspect.signature(hw._enqueue_critical_notification).parameters


# =================== autostop_alert ===================


# channel-down: вызывает notify_recipients (lazy import из worker_notify), НЕ send напрямую
@pytest.mark.asyncio
async def test_autostop_alert_engine_path_uses_recurring_incident(monkeypatch) -> None:
    import core.meta_api.autostop_alert as alert
    from core.meta_api.errors import TemporaryError

    engine = MagicMock()

    exc = TemporaryError("Failed to fetch", code=-2)

    spy_notify = AsyncMock(return_value=True)
    monkeypatch.setattr(alert, "notify_recurring_incident", spy_notify)
    result = await alert.maybe_alert_autostop_channel_down(
        exc=exc,
        fb_ad_id="AD_123",
        engine=engine,
    )

    assert result is True
    spy_notify.assert_awaited_once()


# =================== enable_reco ===================


def _fake_candidate() -> object:
    """Минимальный CandidateRow для теста durable notification."""
    import datetime as dt

    from apps.enable_recommendation_worker.main import CandidateRow

    return CandidateRow(
        ad_id=uuid.uuid4(),
        fb_ad_id="ACT_123_456",
        ad_name="Test Ad",
        campaign_name="Test Campaign",
        adset_name="Test AdSet",
        alert_state="disabled",
        last_transition_at=dt.datetime(2026, 6, 1, 0, 0, tzinfo=dt.timezone.utc),
        snoozed_until=None,
        offer_code="CR2",
        cpa_threshold=None,
    )


def _fake_decision():
    from core.enable_reco.analyzer import RecommendationDecision

    return RecommendationDecision(recommend=True, level="warning", skip_reason=None, snapshot={})


# enable_reco: business worker commits through the durable owner outbox facade.
@pytest.mark.asyncio
async def test_enable_reco_notification_uses_durable_outbox() -> None:
    from apps.enable_recommendation_worker.main import enqueue_recommendation_notification

    connection = MagicMock()
    notify = AsyncMock(return_value=True)
    with patch(
        "apps.enable_recommendation_worker.main.notify_owners_in_transaction",
        notify,
    ):
        result = await enqueue_recommendation_notification(
            connection,
            candidate=_fake_candidate(),
            decision=_fake_decision(),
            recommendation_id=uuid.uuid4(),
        )

    assert result is True
    notify.assert_awaited_once()
    assert notify.await_args.kwargs["event_type"] == "enable_recommendation"
    assert notify.await_args.kwargs["severity"] == "warning"


def test_enable_reco_runtime_has_no_telegram_client_factory() -> None:
    import inspect

    import apps.enable_recommendation_worker.main as worker

    source = inspect.getsource(worker)
    assert "TelegramBotClient" not in source
    assert "_default_tg_factory" not in source


# =================== digest ===================


# digest: deterministic card is committed to the outbox, never sent directly.
@pytest.mark.asyncio
async def test_digest_queues_durable_card_without_telegram_client() -> None:
    from datetime import datetime, timezone

    import apps.digest_scheduler.main as dg
    from core.telegram.notifications import EnqueuedNotification

    now = datetime(2026, 6, 20, 9, 30, tzinfo=timezone.utc)
    window = dg.DigestWindow(hour=9, minute=0)

    engine = MagicMock()
    payload = SimpleNamespace(
        window_start_utc=now,
        money_state="unavailable",
        currency=None,
        total_spend_window=None,
        money_issues=("Нет подтверждённых spend-снимков за окно",),
        alerts_warning_count=0,
        alerts_stop_count=0,
        disable_tasks_succeeded=0,
        disable_tasks_failed=0,
        active_offers_count=1,
        active_ads_count=2,
        top_ads_by_spend=[],
    )
    enqueue = AsyncMock(
        return_value=EnqueuedNotification(event_id=uuid.uuid4(), delivery_count=1, was_created=True)
    )

    with (
        patch("apps.digest_scheduler.main._notification_exists", AsyncMock(return_value=False)),
        patch("apps.digest_scheduler.main.build_digest", AsyncMock(return_value=payload)),
        patch("apps.digest_scheduler.main.enqueue_notification", enqueue),
    ):
        status = await dg.run_one_tick(
            engine=engine,
            now=now,
            window=window,
        )

    assert status == "queued"
    spec = enqueue.await_args.args[1]
    assert spec.audience == "all"
    assert spec.event_type == "daily_digest"

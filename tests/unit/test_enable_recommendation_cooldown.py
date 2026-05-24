# -*- coding: utf-8 -*-
"""Тесты cooldown auto-enable: защита от loop'а auto-stop → auto-enable → auto-stop."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.domain import (
    AlertState,
    EnableRecommendationLevel,
    EnableTaskStatus,
)
from core.enable_recommendations.service import (
    _has_auto_enable_cooldown_block,
    promote_recommendation_to_enable_task,
)


def _snapshot(
    *,
    fb_ad_id: str,
    delivery_status: str,
    offer_id,
    ad_id,
    last_observed_at: datetime | None = None,
    offer_code: str = "OFFER-CD",
    **overrides,
):
    """Создаёт упрощённый snapshot для тестов cooldown."""
    campaign = SimpleNamespace(offer_id=offer_id, offer_code=offer_code, campaign_name="Campaign")
    adset = SimpleNamespace(adset_name="Adset", campaign=campaign)
    fb_ad = SimpleNamespace(ad_name=f"Ad {fb_ad_id}", adset=adset)
    snapshot = SimpleNamespace(
        id=uuid.uuid4(),
        ad_id=ad_id,
        offer_id=offer_id,
        fb_ad_id=fb_ad_id,
        fb_ad=fb_ad,
        delivery_status=delivery_status,
        spend=Decimal("5.00"),
        clicks=4,
        cpc=Decimal("0.1200"),
        outbound_clicks=3,
        outbound_ctr=Decimal("1.10"),
        landing_page_views=2,
        cost_per_landing_page_view=Decimal("2.4000"),
        cpm=Decimal("6.2000"),
        frequency=Decimal("1.3000"),
        leads=0,
        cost_per_lead=None,
        registrations=2,
        cost_per_registration=Decimal("0.4000"),
        deposits=0,
        alert_state=AlertState.DISABLED,
        last_observed_at=last_observed_at or datetime(2026, 4, 25, 9, 0, tzinfo=UTC),
    )
    for key, value in overrides.items():
        setattr(snapshot, key, value)
    return snapshot


def _promotion_session(
    *,
    cabinet_day_started_at: datetime,
    execute_results: list,
    event,
    event_fb_ad,
    snapshot,
):
    """Собирает мок-сессию для promote_recommendation_to_enable_task.

    execute_results — последовательность результатов session.execute после observer_settings.
    """
    observer_result = MagicMock()
    observer_result.scalar_one_or_none.return_value = SimpleNamespace(
        cabinet_day_started_at=cabinet_day_started_at
    )
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[observer_result, *execute_results])
    session.scalar = AsyncMock(side_effect=[event, event_fb_ad, snapshot])
    return session


# Auto-enable заблокирован если был AlertEvent stage=STOP в текущем кабинетном дне.
@pytest.mark.asyncio
async def test_cooldown_blocks_auto_enable_after_stop_alert_in_cabinet_day():
    event_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    offer_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    live_scan = datetime(2026, 4, 25, 10, 0, tzinfo=UTC)
    cabinet_day = live_scan - timedelta(hours=2)

    event = SimpleNamespace(
        id=event_id,
        ad_id=ad_id,
        snapshot_id=snapshot_id,
        live_batch_started_at=live_scan,
    )
    event_fb_ad = SimpleNamespace(
        fb_ad_id="ad-stop-cooldown",
        ad_name="Stop Cooldown Ad",
        adset=SimpleNamespace(campaign=SimpleNamespace(offer_id=offer_id, offer_code="OFFER-CD")),
    )
    snapshot = _snapshot(
        fb_ad_id="ad-stop-cooldown",
        delivery_status="OFF",
        offer_id=offer_id,
        ad_id=ad_id,
        last_observed_at=live_scan,
    )
    snapshot.id = snapshot_id

    # disable lookup пуст, stop lookup — нашёл STOP alert
    empty_disable_result = MagicMock()
    empty_disable_result.first.return_value = None
    stop_result = MagicMock()
    stop_result.scalar_one_or_none.return_value = uuid.uuid4()

    session = _promotion_session(
        cabinet_day_started_at=cabinet_day,
        execute_results=[empty_disable_result, stop_result],
        event=event,
        event_fb_ad=event_fb_ad,
        snapshot=snapshot,
    )

    result = await promote_recommendation_to_enable_task(
        session,
        event_id=event_id,
        requested_by_username="auto",
    )

    assert result.outcome == "blocked_stop_cooldown"
    assert "STOP" in result.detail


# Auto-enable заблокирован если был successful auto-disable от bot_auto_stop.
@pytest.mark.asyncio
async def test_cooldown_blocks_auto_enable_after_auto_disable_task_in_cabinet_day():
    event_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    offer_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    live_scan = datetime(2026, 4, 25, 11, 0, tzinfo=UTC)
    cabinet_day = live_scan - timedelta(hours=3)

    event = SimpleNamespace(
        id=event_id,
        ad_id=ad_id,
        snapshot_id=snapshot_id,
        live_batch_started_at=live_scan,
    )
    event_fb_ad = SimpleNamespace(
        fb_ad_id="ad-auto-disable",
        ad_name="Auto Disable Ad",
        adset=SimpleNamespace(campaign=SimpleNamespace(offer_id=offer_id, offer_code="OFFER-CD")),
    )
    snapshot = _snapshot(
        fb_ad_id="ad-auto-disable",
        delivery_status="OFF",
        offer_id=offer_id,
        ad_id=ad_id,
        last_observed_at=live_scan,
    )
    snapshot.id = snapshot_id

    # disable lookup — нашёл successful task с requested_by_username = bot_auto_stop
    auto_disable_result = MagicMock()
    auto_disable_result.first.return_value = (uuid.uuid4(), "bot_auto_stop")

    session = _promotion_session(
        cabinet_day_started_at=cabinet_day,
        execute_results=[auto_disable_result],
        event=event,
        event_fb_ad=event_fb_ad,
        snapshot=snapshot,
    )

    result = await promote_recommendation_to_enable_task(
        session,
        event_id=event_id,
        requested_by_username="auto",
    )

    assert result.outcome == "blocked_auto_disable_cooldown"
    assert "авто-отключено" in result.detail


# Ручное создание EnableTask через Telegram-кнопку обходит cooldown.
@pytest.mark.asyncio
async def test_cooldown_does_not_block_manual_telegram_enable():
    event_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    offer_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    live_scan = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    cabinet_day = live_scan - timedelta(hours=2)

    event = SimpleNamespace(
        id=event_id,
        ad_id=ad_id,
        snapshot_id=snapshot_id,
        live_batch_started_at=live_scan,
    )
    event_fb_ad = SimpleNamespace(
        fb_ad_id="ad-manual",
        ad_name="Manual Ad",
        adset=SimpleNamespace(campaign=SimpleNamespace(offer_id=offer_id, offer_code="OFFER-CD")),
    )
    snapshot = _snapshot(
        fb_ad_id="ad-manual",
        delivery_status="OFF",
        offer_id=offer_id,
        ad_id=ad_id,
        last_observed_at=live_scan,
    )
    snapshot.id = snapshot_id

    # observer settings возвращает cabinet_day, но cooldown НЕ вызывается
    # т.к. requested_by_username не равен AUTO_ENABLE_REQUEST_USERNAME ("auto").
    observer_result = MagicMock()
    observer_result.scalar_one_or_none.return_value = SimpleNamespace(
        cabinet_day_started_at=cabinet_day
    )
    session = AsyncMock()
    session.execute = AsyncMock(return_value=observer_result)
    # Возвращаем существующий task, чтобы попасть в ветку 'existing' без падения
    # на реальном EnableTask().status — это удобнее, чем мокать сам EnableTask.
    existing_task = SimpleNamespace(
        id=uuid.uuid4(),
        status=EnableTaskStatus.PENDING,
    )
    session.scalar = AsyncMock(side_effect=[event, event_fb_ad, snapshot, existing_task])
    session.add = MagicMock()
    session.flush = AsyncMock()

    # При requested_by_username='tg_user' идёт не auto-путь — cooldown НЕ проверяется.
    with (
        patch(
            "core.enable_recommendations.service.load_live_batch_bounds",
            new=AsyncMock(return_value=(live_scan, live_scan - timedelta(minutes=30))),
        ),
        patch(
            "core.enable_recommendations.service._load_offer_rule_map",
            new=AsyncMock(
                return_value={offer_id: (SimpleNamespace(cpa_amount=Decimal("20")), object())}
            ),
        ),
        patch(
            "core.enable_recommendations.service._evaluate_enable_recommendation",
            return_value=(
                EnableRecommendationLevel.OK,
                SimpleNamespace(
                    stage=None,
                    matched_rule_codes=[],
                    reason_title="Causal",
                    reason_text="OK",
                    matched_hits=[],
                ),
            ),
        ),
    ):
        result = await promote_recommendation_to_enable_task(
            session,
            event_id=event_id,
            requested_by_telegram_user_id="123456",
            requested_by_username="tg_user",
        )

    # Ручной вызов прошёл cooldown (не выпал в blocked_*) и попал в ветку 'existing'.
    assert result.outcome == "existing"
    assert "blocked" not in result.outcome


# В новом кабинетном дне (cabinet_day сменился) cooldown снят — auto-enable проходит.
@pytest.mark.asyncio
async def test_cooldown_cleared_in_new_cabinet_day():
    ad_id = uuid.uuid4()
    # cabinet_day начался прямо сейчас → старые AlertEvent/DisableTask отфильтруются
    new_cabinet_day = datetime(2026, 4, 26, 0, 0, tzinfo=UTC)

    # Запросы в БД находят 0 записей в текущем кабинетном дне
    empty_disable_result = MagicMock()
    empty_disable_result.first.return_value = None
    empty_stop_result = MagicMock()
    empty_stop_result.scalar_one_or_none.return_value = None

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[empty_disable_result, empty_stop_result])

    blocked, reason = await _has_auto_enable_cooldown_block(
        session,
        ad_id=ad_id,
        cabinet_day_started_at=new_cabinet_day,
    )

    assert blocked is False
    assert reason is None


# Manual disable (не от bot_auto_stop) тоже блокирует auto-enable как раньше.
@pytest.mark.asyncio
async def test_cooldown_blocks_auto_enable_after_manual_disable_username():
    ad_id = uuid.uuid4()
    cabinet_day = datetime(2026, 4, 26, 0, 0, tzinfo=UTC)

    manual_disable_result = MagicMock()
    manual_disable_result.first.return_value = (uuid.uuid4(), "tg_user")

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[manual_disable_result])

    blocked, reason = await _has_auto_enable_cooldown_block(
        session,
        ad_id=ad_id,
        cabinet_day_started_at=cabinet_day,
    )

    assert blocked is True
    assert reason == "manual_disable"

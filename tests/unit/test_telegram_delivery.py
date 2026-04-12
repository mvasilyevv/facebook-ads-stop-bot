# -*- coding: utf-8 -*-
"""Тесты delivery-слоя Telegram: lifecycle-рендер и stream-routing."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.domain import DisableTaskStatus, EnableTaskStatus, TelegramNotificationStream
from core.telegram.delivery import (
    TelegramAdMessageContext,
    broadcast_disable_task_runtime_message,
    broadcast_enable_recommendation_message,
    broadcast_observer_runtime_message,
    render_disable_task_queue_message,
    render_disable_task_runtime_message,
    render_enable_task_queue_message,
    render_enable_task_runtime_message,
)
from core.telegram.service import TelegramDestination


# Проверяем, что успешный disable-runtime рендерится новым headline и показывает контекст stop-а.
def test_render_disable_task_runtime_message_includes_hierarchy_and_stop_metrics():
    """Успешный клик выключения должен показывать иерархию и ожидание OFF."""
    message = render_disable_task_runtime_message(
        ad_name="DRC_CR2_CR023",
        fb_ad_id="120242132372770176",
        requested_by_username="bot_auto_stop",
        status=DisableTaskStatus.SUCCEEDED.value,
        context=TelegramAdMessageContext(
            campaign_name="Campaign A",
            adset_name="Adset A",
            reason_title="Дорогой клик",
            reason_text="Цена клика вышла за допустимую границу.",
            matched_rule_codes=["cpc_stop"],
            metrics_json={
                "spend": "12.34",
                "cpc": "0.0900",
                "clicks": 1,
                "rule_summaries": ["CPC 0.09 > стоп 0.08"],
            },
        ),
    )

    assert "✅ <b>Клик по выключению выполнен</b>" in message
    assert "Campaign A" in message
    assert "Adset A" in message
    assert "DRC_CR2_CR023" in message
    assert "Ждём подтверждения OFF" in message
    assert "@bot_auto_stop" in message


# Проверяем, что очередь отключения использует единый формат и явно уводит цепочку в STOP.
def test_render_disable_task_queue_message_uses_stop_chain_copy():
    """Queue-сообщение на отключение должно показывать статус очереди и STOP topic."""
    message = render_disable_task_queue_message(
        ad_name="DRC_CR2_CR023",
        fb_ad_id="120242132372770176",
        requested_by_username="bot_auto_stop",
        created_new=True,
        context=TelegramAdMessageContext(
            campaign_name="Campaign A",
            adset_name="Adset A",
            reason_title="Дорогой клик",
            reason_text="Цена клика вышла за допустимую границу.",
            matched_rule_codes=["cpc_stop"],
            metrics_json={
                "spend": "12.34",
                "clicks": 1,
                "cpc": "0.0900",
                "rule_summaries": ["CPC 0.09 > стоп 0.08"],
            },
        ),
    )

    assert "✅ <b>Создана задача на отключение</b>" in message
    assert "📏 Пороговые детали:" in message
    assert "⏳ Статус: в очереди" in message
    assert "📍 STOP topic" in message
    assert "@bot_auto_stop" in message


# Проверяем, что очередь включения использует единый формат и явно уводит цепочку в ENABLE.
def test_render_enable_task_queue_message_uses_enable_chain_copy():
    """Queue-сообщение на включение должно показывать статус очереди и ENABLE topic."""
    message = render_enable_task_queue_message(
        ad_name="Recovery Ad",
        fb_ad_id="120242132372770177",
        requested_by_username="bot_auto_enable",
        created_new=False,
        context=TelegramAdMessageContext(
            campaign_name="Campaign B",
            adset_name="Adset B",
            reason_title="Можно вернуть в работу",
            reason_text="Проверка пройдена вручную и блокирующих сигналов нет.",
            matched_rule_codes=[],
            metrics_json={"spend": "0.00"},
        ),
    )

    assert "ℹ️ <b>Задача на включение уже была в очереди</b>" in message
    assert "⏳ Статус: ожидает выполнения" in message
    assert "📍 ENABLE topic" in message
    assert "@bot_auto_enable" in message


# Проверяем, что успешный enable-runtime рендерится через единый шаблон и ENABLE topic.
def test_render_enable_task_runtime_message_includes_enable_footer():
    """Успешное включение должно показывать общий шаблон и отметку ENABLE topic."""
    message = render_enable_task_runtime_message(
        ad_name="Recovery Ad",
        fb_ad_id="120242132372770177",
        requested_by_username="bot_auto_enable",
        status=EnableTaskStatus.SUCCEEDED.value,
        context=TelegramAdMessageContext(
            campaign_name="Campaign B",
            adset_name="Adset B",
            reason_title="Можно вернуть в работу",
            reason_text="Проверка пройдена вручную и блокирующих сигналов нет.",
            matched_rule_codes=[],
            metrics_json={"spend": "0.00"},
        ),
    )

    assert "✅ <b>Задача на включение выполнена</b>" in message
    assert "Campaign B" in message
    assert "Adset B" in message
    assert "📍 ENABLE topic" in message
    assert "@bot_auto_enable" in message


# Проверяем, что disable-runtime использует отдельный STOP-stream и сохраняет новый delivery-ref.
@pytest.mark.asyncio
async def test_broadcast_disable_task_runtime_message_uses_stop_stream():
    """Runtime-апдейт отключения должен писать ref только в STOP-поток."""
    destination = TelegramDestination(
        chat_id="-1003701505954",
        telegram_user_id="42",
        role="owner",
        username="owner",
        first_name="Иван",
        is_primary=True,
        delivery_mode="FORUM_GROUP",
        control_topic_id=11,
        early_topic_id=12,
        warning_topic_id=13,
        stop_topic_id=14,
        enable_topic_id=15,
    )

    with (
        patch(
            "core.telegram.delivery.load_telegram_runtime_config",
            new=AsyncMock(return_value=("token", [destination])),
        ),
        patch(
            "core.telegram.delivery.load_message_refs_by_chat",
            new=AsyncMock(return_value={"-1003701505954": 77}),
        ) as refs_mock,
        patch(
            "core.telegram.delivery.safe_edit_or_send_message",
            new=AsyncMock(return_value=("edited", 77)),
        ) as send_mock,
        patch(
            "core.telegram.delivery.upsert_message_ref",
            new=AsyncMock(),
        ) as upsert_mock,
    ):
        await broadcast_disable_task_runtime_message(
            ad_name="Ad 1",
            fb_ad_id="ad-1",
            requested_by_username="owner",
            status=DisableTaskStatus.SUCCEEDED.value,
            incident_key="incident-1",
            context=TelegramAdMessageContext(),
        )

    refs_mock.assert_awaited_once_with(
        fb_ad_id="ad-1",
        incident_key="incident-1",
        stream_kind=TelegramNotificationStream.STOP,
    )
    upsert_mock.assert_awaited_once_with(
        chat_id="-1003701505954",
        message_id=77,
        fb_ad_id="ad-1",
        incident_key="incident-1",
        stream_kind=TelegramNotificationStream.STOP,
    )
    assert send_mock.await_args.kwargs["message_thread_id"] == 14


# Проверяем, что recommendation на включение публикуется в ENABLE-stream с event_id как incident key.
@pytest.mark.asyncio
async def test_broadcast_enable_recommendation_message_uses_enable_stream():
    """Recommendation на включение должна жить в ENABLE-потоке по event_id."""
    destination = TelegramDestination(
        chat_id="-1003701505954",
        telegram_user_id="42",
        role="owner",
        username="owner",
        first_name="Иван",
        is_primary=True,
        delivery_mode="FORUM_GROUP",
        control_topic_id=11,
        early_topic_id=12,
        warning_topic_id=13,
        stop_topic_id=14,
        enable_topic_id=15,
    )

    with (
        patch(
            "core.telegram.delivery.load_telegram_runtime_config",
            new=AsyncMock(return_value=("token", [destination])),
        ),
        patch(
            "core.telegram.delivery.load_message_refs_by_chat",
            new=AsyncMock(return_value={}),
        ) as refs_mock,
        patch(
            "core.telegram.delivery.safe_edit_or_send_message",
            new=AsyncMock(return_value=("sent", 88)),
        ) as send_mock,
        patch(
            "core.telegram.delivery.upsert_message_ref",
            new=AsyncMock(),
        ) as upsert_mock,
    ):
        await broadcast_enable_recommendation_message(
            event_id="event-1",
            ad_name="Ad 2",
            fb_ad_id="ad-2",
            campaign_name="Campaign",
            adset_name="Adset",
            delivery_status="OFF",
            recommendation_level="OK",
            matched_rule_codes=[],
            reason_title="Норма",
            reason_text="Можно включить после проверки.",
            metrics_json={},
        )

    refs_mock.assert_awaited_once_with(
        fb_ad_id="ad-2",
        incident_key="event-1",
        stream_kind=TelegramNotificationStream.ENABLE,
    )
    upsert_mock.assert_awaited_once_with(
        chat_id="-1003701505954",
        message_id=88,
        fb_ad_id="ad-2",
        incident_key="event-1",
        stream_kind=TelegramNotificationStream.ENABLE,
    )
    assert send_mock.await_args.kwargs["message_thread_id"] == 15


# Проверяем, что служебный alert observer идёт в CONTROL topic forum-группы.
@pytest.mark.asyncio
async def test_broadcast_observer_runtime_message_uses_control_topic():
    """Служебное сообщение observer должно уходить в CONTROL topic."""
    destination = TelegramDestination(
        chat_id="-1003701505954",
        telegram_user_id="42",
        role="owner",
        username="owner",
        first_name="Иван",
        is_primary=True,
        delivery_mode="FORUM_GROUP",
        control_topic_id=11,
        early_topic_id=12,
        warning_topic_id=13,
        stop_topic_id=14,
        enable_topic_id=15,
    )
    fake_client = AsyncMock()

    with (
        patch(
            "core.telegram.delivery.load_telegram_runtime_config",
            new=AsyncMock(return_value=("token", [destination])),
        ),
        patch(
            "core.telegram.delivery.TelegramBotClient",
            return_value=fake_client,
        ),
    ):
        await broadcast_observer_runtime_message(text="Служебное сообщение")

    fake_client.send_message.assert_awaited_once_with(
        chat_id="-1003701505954",
        message_thread_id=11,
        text="Служебное сообщение",
    )
    fake_client.close.assert_awaited_once()

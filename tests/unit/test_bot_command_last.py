# -*- coding: utf-8 -*-
"""Тесты команды /last: группировка алертов по кампаниям и адсетам."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_client() -> MagicMock:
    """Создаёт мок TelegramBotClient с нужными async-методами."""
    client = MagicMock()
    client.send_message = AsyncMock(return_value={"message_id": 1})
    return client


def _make_alert_row(
    *,
    ad_name: str,
    campaign_name: str,
    adset_name: str,
    fsm_state: str,
    created_at: datetime,
) -> dict:
    """Создаёт словарь-строку алерта, как возвращает load_recent_alerts_with_context."""
    event = MagicMock()
    event.state = MagicMock()
    event.state.value = fsm_state
    event.created_at = created_at
    return {
        "alert_event": event,
        "fb_ad_id": "abc123",
        "ad_name": ad_name,
        "campaign_name": campaign_name,
        "adset_name": adset_name,
        "fsm_state": fsm_state,
        "created_at": created_at,
    }


def _make_session_factory():
    """Создаёт мок фабрики сессий."""
    mock_session = AsyncMock()
    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_factory


# Проверяем группировку по кампаниям: в тексте должны быть имена двух разных кампаний.
@pytest.mark.asyncio
async def test_cmd_last_groups_by_campaign():
    """_cmd_last должна группировать алерты по кампаниям (заголовок 🎯 для каждой)."""
    from core.telegram.bot_handler import _cmd_last

    client = _make_client()

    rows = [
        _make_alert_row(
            ad_name="CR001",
            campaign_name="CR2 | DRC | MV",
            adset_name="Tyver | RU",
            fsm_state="STOP_SENT",
            created_at=datetime(2024, 1, 1, 14, 51, tzinfo=UTC),
        ),
        _make_alert_row(
            ad_name="CR002",
            campaign_name="CR1 | DRC | MV",
            adset_name="Tyver | UA",
            fsm_state="DISABLED",
            created_at=datetime(2024, 1, 1, 7, 40, tzinfo=UTC),
        ),
    ]

    captured: list[str] = []

    async def capture_send(*args, **kwargs):
        captured.append(kwargs.get("text", ""))

    with (
        patch(
            "core.telegram.bot_handler.get_session_factory", return_value=_make_session_factory()
        ),
        patch(
            "core.telegram.bot_handler._send_current_topic_message",
            side_effect=capture_send,
        ),
        patch("core.config.get_settings", return_value=MagicMock(app_timezone="UTC")),
        patch(
            "core.observer.db_queries.load_recent_alerts_with_context",
            new_callable=AsyncMock,
            return_value=rows,
        ),
    ):
        await _cmd_last(client, chat_id="123", message_thread_id=None, parts=["/last"])

    assert captured, "Ожидалось хотя бы одно сообщение"
    text = captured[0]
    assert "CR2 | DRC | MV" in text, "Ожидалась кампания CR2"
    assert "CR1 | DRC | MV" in text, "Ожидалась кампания CR1"


# Проверяем, что в выводе присутствуют эмодзи для состояний STOP_SENT и DISABLED.
@pytest.mark.asyncio
async def test_cmd_last_shows_state_emojis():
    """_cmd_last должна показывать эмодзи ⛔ для DISABLED и 🛑 для STOP_SENT."""
    from core.telegram.bot_handler import _cmd_last

    client = _make_client()

    rows = [
        _make_alert_row(
            ad_name="DRC_CR2_CR012",
            campaign_name="CR2 | DRC | MV",
            adset_name="Tyver | RU",
            fsm_state="STOP_SENT",
            created_at=datetime(2024, 1, 1, 14, 51, tzinfo=UTC),
        ),
        _make_alert_row(
            ad_name="DRC_CR2_CR005",
            campaign_name="CR2 | DRC | MV",
            adset_name="Tyver | UA",
            fsm_state="DISABLED",
            created_at=datetime(2024, 1, 1, 7, 40, tzinfo=UTC),
        ),
    ]

    captured: list[str] = []

    async def capture_send(*args, **kwargs):
        captured.append(kwargs.get("text", ""))

    with (
        patch(
            "core.telegram.bot_handler.get_session_factory", return_value=_make_session_factory()
        ),
        patch(
            "core.telegram.bot_handler._send_current_topic_message",
            side_effect=capture_send,
        ),
        patch("core.config.get_settings", return_value=MagicMock(app_timezone="UTC")),
        patch(
            "core.observer.db_queries.load_recent_alerts_with_context",
            new_callable=AsyncMock,
            return_value=rows,
        ),
    ):
        await _cmd_last(client, chat_id="123", message_thread_id=None, parts=["/last"])

    assert captured
    text = captured[0]
    assert "🛑" in text, "Ожидался эмодзи 🛑 для STOP_SENT"
    assert "⛔" in text, "Ожидался эмодзи ⛔ для DISABLED"


# Проверяем группировку по адсетам внутри одной кампании.
@pytest.mark.asyncio
async def test_cmd_last_groups_adsets_within_campaign():
    """_cmd_last должна показывать адсеты внутри кампании с иконкой 📁."""
    from core.telegram.bot_handler import _cmd_last

    client = _make_client()

    rows = [
        _make_alert_row(
            ad_name="DRC_CR2_CR011",
            campaign_name="CR2 | DRC",
            adset_name="Tyver | RU",
            fsm_state="WARNING_SENT",
            created_at=datetime(2024, 1, 1, 14, 49, tzinfo=UTC),
        ),
        _make_alert_row(
            ad_name="DRC_CR2_CR012",
            campaign_name="CR2 | DRC",
            adset_name="Tyver | UA",
            fsm_state="STOP_SENT",
            created_at=datetime(2024, 1, 1, 14, 51, tzinfo=UTC),
        ),
    ]

    captured: list[str] = []

    async def capture_send(*args, **kwargs):
        captured.append(kwargs.get("text", ""))

    with (
        patch(
            "core.telegram.bot_handler.get_session_factory", return_value=_make_session_factory()
        ),
        patch(
            "core.telegram.bot_handler._send_current_topic_message",
            side_effect=capture_send,
        ),
        patch("core.config.get_settings", return_value=MagicMock(app_timezone="UTC")),
        patch(
            "core.observer.db_queries.load_recent_alerts_with_context",
            new_callable=AsyncMock,
            return_value=rows,
        ),
    ):
        await _cmd_last(client, chat_id="123", message_thread_id=None, parts=["/last"])

    assert captured
    text = captured[0]
    assert "📁" in text, "Ожидался эмодзи 📁 для адсетов"
    assert "Tyver | RU" in text
    assert "Tyver | UA" in text


# Проверяем фолбэк при отсутствии алертов — сообщение об их отсутствии.
@pytest.mark.asyncio
async def test_cmd_last_empty():
    """_cmd_last должна отправить сообщение об отсутствии алертов, если список пуст."""
    from core.telegram.bot_handler import _cmd_last

    client = _make_client()

    captured: list[str] = []

    async def capture_send(*args, **kwargs):
        captured.append(kwargs.get("text", ""))

    with (
        patch(
            "core.telegram.bot_handler.get_session_factory", return_value=_make_session_factory()
        ),
        patch(
            "core.telegram.bot_handler._send_current_topic_message",
            side_effect=capture_send,
        ),
        patch("core.config.get_settings", return_value=MagicMock(app_timezone="UTC")),
        patch(
            "core.observer.db_queries.load_recent_alerts_with_context",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        await _cmd_last(client, chat_id="123", message_thread_id=None, parts=["/last"])

    assert captured
    text = captured[0]
    assert "нет" in text.lower()

# -*- coding: utf-8 -*-
"""Тесты новых команд Wave A.3 в bot_handler: /health, /pause, /resume, /reconnect,
/last, /why, /app."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_client() -> MagicMock:
    """Создаёт мок TelegramBotClient с нужными async-методами."""
    client = MagicMock()
    client.send_message = AsyncMock(return_value={"message_id": 1})
    client.edit_message_text = AsyncMock()
    return client


# Проверяем, что _cmd_health отправляет сообщение с текстом "Состояние".
@pytest.mark.asyncio
async def test_cmd_health_sends_status_message():
    """_cmd_health должна отправить сообщение с информацией о здоровье системы."""
    from core.telegram.bot_handler import _cmd_health

    client = _make_client()

    fake_worker = MagicMock()
    fake_worker.healthy = True
    fake_worker.heartbeat_age_seconds = 10.0
    fake_worker.error = None

    fake_details = MagicMock()
    fake_details.workers = {"observer": fake_worker, "telegram_poller": fake_worker}
    fake_details.browser_agent = MagicMock(healthy=True, error=None)
    fake_details.vision = MagicMock(healthy=True, error=None)
    fake_details.queues = MagicMock(
        disable_pending=0, disable_running=0, enable_pending=0, enable_running=0
    )
    fake_details.last_successful_scan = MagicMock(age_seconds=30.0)

    mock_session = AsyncMock()
    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("core.telegram.bot_handler.get_session_factory", return_value=mock_factory),
        patch(
            "apps.api.routers.health.collect_health_details",
            new_callable=AsyncMock,
            return_value=fake_details,
        ),
        patch(
            "core.telegram.bot_handler._send_current_topic_message",
            new_callable=AsyncMock,
        ) as mock_send,
    ):
        await _cmd_health(client, chat_id="123", message_thread_id=None)

    mock_send.assert_called_once()
    text = mock_send.call_args.kwargs.get("text", "")
    assert "Состояние" in text or "Observer" in text or "heartbeat" in text


# Проверяем, что _cmd_pause отключает сканирование и задаёт pause_until.
@pytest.mark.asyncio
async def test_cmd_pause_disables_scanning():
    """_cmd_pause должна установить is_scanning_enabled=False и задать pause_until."""
    from core.telegram.bot_handler import _cmd_pause

    client = _make_client()

    mock_settings = MagicMock()
    mock_settings.is_scanning_enabled = True
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("core.telegram.bot_handler.get_session_factory", return_value=mock_factory),
        patch(
            "core.settings_queries.get_or_create_observer_settings",
            new_callable=AsyncMock,
            return_value=mock_settings,
        ),
        patch(
            "core.telegram.bot_handler._send_current_topic_message",
            new_callable=AsyncMock,
        ) as mock_send,
    ):
        await _cmd_pause(client, chat_id="123", message_thread_id=None, parts=["/pause", "30"])

    assert mock_settings.is_scanning_enabled is False
    assert mock_settings.pause_until is not None
    mock_send.assert_called_once()
    text = mock_send.call_args.kwargs.get("text", "")
    assert "30" in text or "паузу" in text.lower() or "Пауза" in text


# Проверяем, что _cmd_pause с дефолтными 15 минутами работает без аргументов.
@pytest.mark.asyncio
async def test_cmd_pause_default_15_minutes():
    """_cmd_pause без аргументов должна поставить паузу на 15 минут."""
    from core.telegram.bot_handler import _cmd_pause

    client = _make_client()

    mock_settings = MagicMock()
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("core.telegram.bot_handler.get_session_factory", return_value=mock_factory),
        patch(
            "core.settings_queries.get_or_create_observer_settings",
            new_callable=AsyncMock,
            return_value=mock_settings,
        ),
        patch(
            "core.telegram.bot_handler._send_current_topic_message",
            new_callable=AsyncMock,
        ) as mock_send,
    ):
        await _cmd_pause(client, chat_id="123", message_thread_id=None, parts=["/pause"])

    assert mock_settings.is_scanning_enabled is False
    mock_send.assert_called_once()
    text = mock_send.call_args.kwargs.get("text", "")
    assert "15" in text or "паузу" in text.lower() or "Пауза" in text


# Проверяем, что _cmd_resume включает сканирование и сбрасывает pause_until.
@pytest.mark.asyncio
async def test_cmd_resume_enables_scanning():
    """_cmd_resume должна установить is_scanning_enabled=True и pause_until=None."""
    from core.telegram.bot_handler import _cmd_resume

    client = _make_client()

    mock_settings = MagicMock()
    mock_settings.is_scanning_enabled = False
    mock_settings.pause_until = datetime.now(UTC) + timedelta(hours=1)
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("core.telegram.bot_handler.get_session_factory", return_value=mock_factory),
        patch(
            "core.settings_queries.get_or_create_observer_settings",
            new_callable=AsyncMock,
            return_value=mock_settings,
        ),
        patch(
            "core.telegram.bot_handler._send_current_topic_message",
            new_callable=AsyncMock,
        ) as mock_send,
    ):
        await _cmd_resume(client, chat_id="123", message_thread_id=None)

    assert mock_settings.is_scanning_enabled is True
    assert mock_settings.pause_until is None
    mock_send.assert_called_once()


# Проверяем, что _cmd_reconnect отправляет сообщение с inline-клавиатурой.
@pytest.mark.asyncio
async def test_cmd_reconnect_sends_confirm_keyboard():
    """_cmd_reconnect должна отправить сообщение с кнопками confirm/cancel."""
    from core.telegram.bot_handler import _cmd_reconnect

    client = _make_client()

    with patch(
        "core.telegram.bot_handler._send_current_topic_message",
        new_callable=AsyncMock,
    ) as mock_send:
        await _cmd_reconnect(client, chat_id="123", message_thread_id=None)

    mock_send.assert_called_once()
    markup = mock_send.call_args.kwargs.get("reply_markup", {})
    assert "inline_keyboard" in markup
    # Первый ряд содержит два элемента
    row = markup["inline_keyboard"][0]
    callback_datas = [btn["callback_data"] for btn in row]
    assert any("reconnect_confirm:" in d for d in callback_datas)
    assert any("reconnect_cancel:" in d for d in callback_datas)


# Проверяем, что _cmd_app отправляет ссылку если web_app_url задан.
@pytest.mark.asyncio
async def test_cmd_app_sends_webapp_link_when_configured():
    """_cmd_app должна отправить сообщение с web_app кнопкой если WEB_APP_URL задан."""
    from core.telegram.bot_handler import _cmd_app

    client = _make_client()

    with (
        patch("core.config.get_settings") as mock_get_settings,
        patch(
            "core.telegram.bot_handler._send_current_topic_message",
            new_callable=AsyncMock,
        ) as mock_send,
    ):
        mock_cfg = MagicMock()
        mock_cfg.web_app_url = "https://example.com/app"
        mock_get_settings.return_value = mock_cfg

        await _cmd_app(client, chat_id="123", message_thread_id=None)

    mock_send.assert_called_once()
    markup = mock_send.call_args.kwargs.get("reply_markup", {})
    assert "inline_keyboard" in markup
    btn = markup["inline_keyboard"][0][0]
    assert btn.get("web_app", {}).get("url") == "https://example.com/app"


# Проверяем, что _cmd_app сообщает об отсутствии конфигурации если web_app_url не задан.
@pytest.mark.asyncio
async def test_cmd_app_reports_not_configured_when_no_url():
    """_cmd_app должна отправить сообщение об отсутствии настройки."""
    from core.telegram.bot_handler import _cmd_app

    client = _make_client()

    with (
        patch("core.config.get_settings") as mock_get_settings,
        patch(
            "core.telegram.bot_handler._send_current_topic_message",
            new_callable=AsyncMock,
        ) as mock_send,
    ):
        mock_cfg = MagicMock()
        mock_cfg.web_app_url = None
        mock_get_settings.return_value = mock_cfg

        await _cmd_app(client, chat_id="123", message_thread_id=None)

    mock_send.assert_called_once()
    text = mock_send.call_args.kwargs.get("text", "")
    assert "не настроена" in text or "WEB_APP_URL" in text

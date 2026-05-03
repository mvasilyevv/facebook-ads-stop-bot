# -*- coding: utf-8 -*-
"""Тесты упрощённого bot_handler: /start, /help, /app, неизвестные команды."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_client() -> MagicMock:
    """Создаёт мок TelegramBotClient с нужными async-методами."""
    client = MagicMock()
    client.send_message = AsyncMock(return_value={"message_id": 1})
    return client


# Проверяем, что /start отвечает текстом про приложение и возвращает web_app кнопку.
@pytest.mark.asyncio
async def test_render_start_with_web_app_url():
    """/start должна отправить приветствие с web_app кнопкой если WEB_APP_URL задан."""
    from core.telegram.bot_handler import _render_start

    client = _make_client()

    with (
        patch(
            "core.telegram.service.load_web_app_url",
            new=AsyncMock(return_value="https://example.com/app"),
        ),
        patch(
            "core.telegram.bot_handler._send_current_topic_message",
            new_callable=AsyncMock,
        ) as mock_send,
    ):
        await _render_start(client, chat_id="123", message_thread_id=None)

    mock_send.assert_called_once()
    text = mock_send.call_args.kwargs.get("text", "")
    assert "FB Stop Bot" in text or "приложение" in text.lower()
    markup = mock_send.call_args.kwargs.get("reply_markup", {})
    assert markup is not None
    assert "inline_keyboard" in markup
    btn = markup["inline_keyboard"][0][0]
    assert btn.get("web_app", {}).get("url") == "https://example.com/app"


# Проверяем, что /start без WEB_APP_URL не добавляет кнопку.
@pytest.mark.asyncio
async def test_render_start_without_web_app_url():
    """/start без WEB_APP_URL должна отправить текст без inline-кнопки."""
    from core.telegram.bot_handler import _render_start

    client = _make_client()

    with (
        patch(
            "core.telegram.service.load_web_app_url",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "core.telegram.bot_handler._send_current_topic_message",
            new_callable=AsyncMock,
        ) as mock_send,
    ):
        await _render_start(client, chat_id="123", message_thread_id=None)

    mock_send.assert_called_once()
    markup = mock_send.call_args.kwargs.get("reply_markup")
    assert markup is None


# Проверяем, что /help отвечает текстом с тремя командами.
@pytest.mark.asyncio
async def test_render_help_lists_three_commands():
    """/help должна отправить справку с тремя командами: /start, /app, /help."""
    from core.telegram.bot_handler import _render_help

    client = _make_client()

    with patch(
        "core.telegram.bot_handler._send_current_topic_message",
        new_callable=AsyncMock,
    ) as mock_send:
        await _render_help(client, chat_id="123", message_thread_id=None)

    mock_send.assert_called_once()
    text = mock_send.call_args.kwargs.get("text", "")
    assert "/start" in text
    assert "/app" in text
    assert "/help" in text


# Проверяем, что удалённые команды /status, /ads и пр. отвечают «не поддерживается».
@pytest.mark.asyncio
async def test_unknown_command_returns_unsupported_message():
    """Удалённые команды должны возвращать сообщение 'Команда не поддерживается'."""
    from core.telegram.bot_handler import handle_update

    client = _make_client()

    def _make_msg(cmd: str) -> dict:
        return {
            "message": {
                "chat": {"id": -100123456789, "type": "supergroup"},
                "message_thread_id": 10,
                "text": f"/{cmd}",
                "from": {"id": 999, "username": "tester"},
            }
        }

    fake_access = MagicMock()
    fake_access.role = "owner"

    with (
        patch(
            "core.telegram.bot_handler.resolve_telegram_access",
            new_callable=AsyncMock,
            return_value=fake_access,
        ),
        patch(
            "core.telegram.bot_handler._send_current_topic_message",
            new_callable=AsyncMock,
        ) as mock_send,
    ):
        await handle_update(client, _make_msg("status"))

    mock_send.assert_called_once()
    text = mock_send.call_args.kwargs.get("text", "")
    assert "не поддерживается" in text.lower() or "/app" in text

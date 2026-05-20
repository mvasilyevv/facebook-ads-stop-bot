# -*- coding: utf-8 -*-
"""Тесты команды /bind_thread в Telegram-боте."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.domain import TelegramUserRole


def _make_factory(session_mock):
    """Собирает фейковую фабрику async-сессии вокруг подготовленного session_mock."""
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session_mock)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


# Owner вызывает /bind_thread STOP из топика — настройка должна сохраниться, бот ответить подтверждением.
@pytest.mark.asyncio
async def test_bind_thread_owner_happy_path_saves_thread_id():
    from core.telegram.bot_handler import _handle_bind_thread

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)
    settings_row = SimpleNamespace(
        thread_id_warning=None,
        thread_id_stop=None,
        thread_id_enable=None,
        thread_id_ops=None,
        thread_id_general=None,
    )

    session = AsyncMock()
    session.commit = AsyncMock()
    factory = _make_factory(session)

    with (
        patch("core.telegram.bot_handler.get_session_factory", return_value=factory),
        patch(
            "core.telegram.bot_handler.get_or_create_telegram_settings",
            AsyncMock(return_value=settings_row),
        ),
    ):
        await _handle_bind_thread(
            client,
            chat_id="-1001234567890",
            chat_type="supergroup",
            message_thread_id=42,
            access=access,
            parts=["/bind_thread", "stop"],
        )

    assert settings_row.thread_id_stop == 42
    session.commit.assert_awaited_once()
    client.send_message.assert_awaited_once()
    sent_kwargs = client.send_message.await_args.kwargs
    assert sent_kwargs["message_thread_id"] == 42
    assert "STOP" in sent_kwargs["text"]


# Non-owner не должен иметь доступа к /bind_thread — бот шлёт OWNER_ONLY_TEXT, БД не трогается.
@pytest.mark.asyncio
async def test_bind_thread_non_owner_rejected():
    from core.telegram.bot_handler import OWNER_ONLY_TEXT, _handle_bind_thread

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.RECIPIENT.value)

    with patch("core.telegram.bot_handler.get_session_factory") as factory_mock:
        await _handle_bind_thread(
            client,
            chat_id="-1001234567890",
            chat_type="supergroup",
            message_thread_id=42,
            access=access,
            parts=["/bind_thread", "STOP"],
        )

    factory_mock.assert_not_called()
    client.send_message.assert_awaited_once()
    assert client.send_message.await_args.kwargs["text"] == OWNER_ONLY_TEXT


# Команда вне форумного топика (нет message_thread_id) — бот отвечает подсказкой и ничего не пишет в БД.
@pytest.mark.asyncio
async def test_bind_thread_outside_topic_returns_hint():
    from core.telegram.bot_handler import _handle_bind_thread

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)

    with patch("core.telegram.bot_handler.get_session_factory") as factory_mock:
        await _handle_bind_thread(
            client,
            chat_id="-1001234567890",
            chat_type="supergroup",
            message_thread_id=None,
            access=access,
            parts=["/bind_thread", "STOP"],
        )

    factory_mock.assert_not_called()
    client.send_message.assert_awaited_once()
    assert "форумного топика" in client.send_message.await_args.kwargs["text"]


# Неизвестный стрим — бот возвращает usage и ничего не сохраняет.
@pytest.mark.asyncio
async def test_bind_thread_unknown_stream_returns_usage():
    from core.telegram.bot_handler import _BIND_THREAD_USAGE, _handle_bind_thread

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)

    with patch("core.telegram.bot_handler.get_session_factory") as factory_mock:
        await _handle_bind_thread(
            client,
            chat_id="-1001234567890",
            chat_type="supergroup",
            message_thread_id=42,
            access=access,
            parts=["/bind_thread", "UNKNOWN"],
        )

    factory_mock.assert_not_called()
    client.send_message.assert_awaited_once()
    assert client.send_message.await_args.kwargs["text"] == _BIND_THREAD_USAGE

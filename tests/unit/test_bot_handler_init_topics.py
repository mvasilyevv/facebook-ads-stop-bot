# -*- coding: utf-8 -*-
"""Тесты команды /init_topics — массовое создание форумных топиков."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.domain import TelegramUserRole


def _make_factory(session_mock):
    """Фейковая фабрика async-сессии вокруг подготовленного session_mock."""
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session_mock)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


def _empty_settings():
    """telegram_settings с пустыми привязками."""
    return SimpleNamespace(
        thread_id_warning=None,
        thread_id_stop=None,
        thread_id_enable=None,
        thread_id_ops=None,
        thread_id_general=None,
    )


# Owner вызывает /init_topics в супергруппе — бот создаёт 4 топика и проставляет General=1.
@pytest.mark.asyncio
async def test_init_topics_creates_missing_and_binds_general():
    from core.telegram.bot_handler import _handle_init_topics

    client = AsyncMock()
    # createForumTopic возвращает уникальный thread_id для каждого вызова
    client.create_forum_topic = AsyncMock(
        side_effect=[
            {"message_thread_id": 101},
            {"message_thread_id": 102},
            {"message_thread_id": 103},
            {"message_thread_id": 104},
        ]
    )

    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)
    settings_row = _empty_settings()
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
        await _handle_init_topics(
            client,
            chat_id="-1001234567890",
            chat_type="supergroup",
            message_thread_id=None,
            access=access,
        )

    assert client.create_forum_topic.await_count == 4
    assert settings_row.thread_id_warning == 101
    assert settings_row.thread_id_stop == 102
    assert settings_row.thread_id_enable == 103
    assert settings_row.thread_id_ops == 104
    assert settings_row.thread_id_general == 1
    session.commit.assert_awaited_once()
    client.send_message.assert_awaited_once()
    assert "Созданы" in client.send_message.await_args.kwargs["text"]


# Если STOP-топик уже привязан — повторно не создаётся, остальные создаются.
@pytest.mark.asyncio
async def test_init_topics_skips_already_bound_streams():
    from core.telegram.bot_handler import _handle_init_topics

    client = AsyncMock()
    client.create_forum_topic = AsyncMock(
        side_effect=[
            {"message_thread_id": 201},  # WARNING
            {"message_thread_id": 203},  # ENABLE
            {"message_thread_id": 204},  # OPS
        ]
    )

    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)
    settings_row = _empty_settings()
    settings_row.thread_id_stop = 999  # уже привязан
    settings_row.thread_id_general = 7  # уже привязан
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
        await _handle_init_topics(
            client,
            chat_id="-1001234567890",
            chat_type="supergroup",
            message_thread_id=None,
            access=access,
        )

    # STOP не пересоздавался
    assert client.create_forum_topic.await_count == 3
    assert settings_row.thread_id_stop == 999
    assert settings_row.thread_id_general == 7  # не перезатёрся
    assert settings_row.thread_id_warning == 201
    assert settings_row.thread_id_enable == 203
    assert settings_row.thread_id_ops == 204


# Non-owner не имеет права вызывать /init_topics — бот шлёт OWNER_ONLY_TEXT.
@pytest.mark.asyncio
async def test_init_topics_non_owner_rejected():
    from core.telegram.bot_handler import OWNER_ONLY_TEXT, _handle_init_topics

    client = AsyncMock()
    client.create_forum_topic = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.RECIPIENT.value)

    with patch("core.telegram.bot_handler.get_session_factory") as factory_mock:
        await _handle_init_topics(
            client,
            chat_id="-1001234567890",
            chat_type="supergroup",
            message_thread_id=None,
            access=access,
        )

    factory_mock.assert_not_called()
    client.create_forum_topic.assert_not_called()
    client.send_message.assert_awaited_once()
    assert client.send_message.await_args.kwargs["text"] == OWNER_ONLY_TEXT


# В обычном чате (не супергруппе) команда отклоняется.
@pytest.mark.asyncio
async def test_init_topics_rejected_outside_supergroup():
    from core.telegram.bot_handler import _handle_init_topics

    client = AsyncMock()
    client.create_forum_topic = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)

    with patch("core.telegram.bot_handler.get_session_factory") as factory_mock:
        await _handle_init_topics(
            client,
            chat_id="123456",
            chat_type="private",
            message_thread_id=None,
            access=access,
        )

    factory_mock.assert_not_called()
    client.create_forum_topic.assert_not_called()
    client.send_message.assert_awaited_once()
    assert "супергруппе" in client.send_message.await_args.kwargs["text"]


# Если createForumTopic упал TelegramAPIError — остальные топики всё равно создаются, ошибка попадает в отчёт.
@pytest.mark.asyncio
async def test_init_topics_continues_on_api_error():
    from core.telegram.bot_handler import _handle_init_topics
    from core.telegram.client import TelegramAPIError

    client = AsyncMock()
    client.create_forum_topic = AsyncMock(
        side_effect=[
            TelegramAPIError(method="createForumTopic", description="not enough rights"),
            {"message_thread_id": 302},
            {"message_thread_id": 303},
            {"message_thread_id": 304},
        ]
    )

    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)
    settings_row = _empty_settings()
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
        await _handle_init_topics(
            client,
            chat_id="-1001234567890",
            chat_type="supergroup",
            message_thread_id=None,
            access=access,
        )

    assert settings_row.thread_id_warning is None  # упал
    assert settings_row.thread_id_stop == 302
    assert settings_row.thread_id_enable == 303
    assert settings_row.thread_id_ops == 304
    session.commit.assert_awaited_once()
    sent = client.send_message.await_args.kwargs["text"]
    assert "Ошибки" in sent
    assert "WARNING" in sent

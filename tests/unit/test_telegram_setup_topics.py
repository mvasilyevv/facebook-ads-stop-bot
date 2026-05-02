# -*- coding: utf-8 -*-
"""Тесты команды /setup_topics: создание forum topics и сохранение thread_id."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_settings():
    """Заглушка TelegramSettings с полями для forum topics."""
    s = MagicMock()
    s.forum_topics_enabled = False
    s.topic_alerts_thread_id = None
    s.topic_disabled_thread_id = None
    s.topic_recommendations_thread_id = None
    s.topic_ops_thread_id = None
    s.topic_logs_thread_id = None
    return s


@pytest.fixture
def mock_db_session(mock_settings):
    """Мок async-сессии БД, возвращающей mock_settings."""
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=mock_settings)
    session.commit = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


@pytest.fixture
def mock_session_factory(mock_db_session):
    """Мок фабрики сессий."""
    factory = MagicMock(return_value=mock_db_session)
    return factory


# Проверяем успешный сценарий: getChat возвращает is_forum=True, createForumTopic создаёт 5 топиков.
@pytest.mark.asyncio
async def test_setup_topics_success(mock_settings, mock_session_factory):
    from core.telegram.bot_handler import _handle_setup_topics

    chat_id = "-1001234567890"
    thread_ids = [101, 202, 303, 404, 505]

    # Настраиваем мок TelegramBotClient
    client = AsyncMock()
    client.get_chat = AsyncMock(return_value={"is_forum": True})
    # create_forum_topic возвращает разные thread_id для каждого вызова
    client.create_forum_topic = AsyncMock(
        side_effect=[{"message_thread_id": tid} for tid in thread_ids]
    )
    client.send_message = AsyncMock(return_value={"message_id": 1})

    with (
        patch(
            "core.telegram.bot_handler.get_session_factory",
            return_value=mock_session_factory,
        ),
        patch(
            "core.telegram.bot_handler.get_or_create_telegram_settings",
            new_callable=AsyncMock,
            return_value=mock_settings,
        ),
    ):
        await _handle_setup_topics(client, chat_id=chat_id, message_thread_id=1)

    # Проверяем, что createForumTopic вызывался 5 раз
    assert client.create_forum_topic.call_count == 5

    # Проверяем, что все thread_id записаны в settings
    assert mock_settings.topic_alerts_thread_id == 101
    assert mock_settings.topic_disabled_thread_id == 202
    assert mock_settings.topic_recommendations_thread_id == 303
    assert mock_settings.topic_ops_thread_id == 404
    assert mock_settings.topic_logs_thread_id == 505

    # Проверяем, что флаг forum_topics_enabled стал True
    assert mock_settings.forum_topics_enabled is True

    # Проверяем, что commit был вызван
    mock_session_factory.return_value.commit.assert_awaited()


# Проверяем отказ: getChat возвращает is_forum=False — топики не создаются.
@pytest.mark.asyncio
async def test_setup_topics_not_forum(mock_settings, mock_session_factory):
    from core.telegram.bot_handler import _handle_setup_topics

    chat_id = "-1001234567890"

    client = AsyncMock()
    client.get_chat = AsyncMock(return_value={"is_forum": False})
    client.create_forum_topic = AsyncMock()
    client.send_message = AsyncMock(return_value={"message_id": 1})

    with (
        patch(
            "core.telegram.bot_handler.get_session_factory",
            return_value=mock_session_factory,
        ),
        patch(
            "core.telegram.bot_handler.get_or_create_telegram_settings",
            new_callable=AsyncMock,
            return_value=mock_settings,
        ),
    ):
        await _handle_setup_topics(client, chat_id=chat_id, message_thread_id=1)

    # Топики не должны создаваться
    client.create_forum_topic.assert_not_called()
    # forum_topics_enabled остался False
    assert mock_settings.forum_topics_enabled is False

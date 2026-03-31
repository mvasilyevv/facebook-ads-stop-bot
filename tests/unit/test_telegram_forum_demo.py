# -*- coding: utf-8 -*-
"""Тесты demo-раннера forum topics для Telegram."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


# Проверяем, что раннер создаёт 4 topic и отправляет в каждый по одному сообщению.
@pytest.mark.asyncio
async def test_run_forum_demo_creates_topics_and_sends_messages():
    from run_telegram_forum_demo import run_forum_demo

    fake_client = AsyncMock()
    fake_client.get_chat = AsyncMock(
        return_value={"id": -100123, "type": "supergroup", "is_forum": True, "title": "Demo"}
    )
    fake_client.create_forum_topic = AsyncMock(
        side_effect=[
            {"message_thread_id": 101},
            {"message_thread_id": 102},
            {"message_thread_id": 103},
            {"message_thread_id": 104},
        ]
    )
    fake_client.send_message = AsyncMock(
        side_effect=[
            {"message_id": 201},
            {"message_id": 202},
            {"message_id": 203},
            {"message_id": 204},
        ]
    )
    fake_client.close = AsyncMock()

    with (
        patch("run_telegram_forum_demo._load_runtime_token", new=AsyncMock(return_value="token")),
        patch("run_telegram_forum_demo.TelegramBotClient", return_value=fake_client),
    ):
        await run_forum_demo(chat_id="-100123")

    assert fake_client.create_forum_topic.await_count == 4
    assert fake_client.send_message.await_count == 4
    fake_client.close.assert_awaited_once()


# Проверяем, что раннер отклоняет обычный чат без forum topics.
@pytest.mark.asyncio
async def test_run_forum_demo_rejects_non_forum_chat():
    from run_telegram_forum_demo import run_forum_demo

    fake_client = AsyncMock()
    fake_client.get_chat = AsyncMock(return_value={"id": 123, "type": "private"})
    fake_client.close = AsyncMock()

    with (
        patch("run_telegram_forum_demo._load_runtime_token", new=AsyncMock(return_value="token")),
        patch("run_telegram_forum_demo.TelegramBotClient", return_value=fake_client),
    ):
        with pytest.raises(RuntimeError, match="ожидается supergroup"):
            await run_forum_demo(chat_id="123")

    fake_client.close.assert_awaited_once()

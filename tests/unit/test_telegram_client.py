# -*- coding: utf-8 -*-
"""Тесты Telegram Bot API клиента."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.telegram.client import TelegramBotClient


# Проверяем, что send_message передаёт message_thread_id в Telegram API.
@pytest.mark.asyncio
async def test_send_message_includes_message_thread_id():
    http_client = AsyncMock()
    response = MagicMock()
    response.json.return_value = {"ok": True, "result": {"message_id": 1}}
    response.raise_for_status.return_value = None
    http_client.post = AsyncMock(return_value=response)

    client = TelegramBotClient("token", http_client=http_client)
    try:
        await client.send_message(
            chat_id="-100123",
            message_thread_id=777,
            text="Тест",
        )
    finally:
        await client.close()

    payload = http_client.post.await_args.kwargs["json"]
    assert payload["chat_id"] == "-100123"
    assert payload["message_thread_id"] == 777
    assert payload["text"] == "Тест"


# Проверяем, что create_forum_topic вызывает нужный метод Bot API.
@pytest.mark.asyncio
async def test_create_forum_topic_calls_bot_api():
    http_client = AsyncMock()
    response = MagicMock()
    response.json.return_value = {
        "ok": True,
        "result": {"message_thread_id": 555, "name": "DEMO TOPIC"},
    }
    response.raise_for_status.return_value = None
    http_client.post = AsyncMock(return_value=response)

    client = TelegramBotClient("token", http_client=http_client)
    try:
        result = await client.create_forum_topic(
            chat_id="-100123",
            name="DEMO TOPIC",
            icon_color=0x6FB9F0,
        )
    finally:
        await client.close()

    assert result["message_thread_id"] == 555
    assert http_client.post.await_args.args[0].endswith("/createForumTopic")
    payload = http_client.post.await_args.kwargs["json"]
    assert payload["chat_id"] == "-100123"
    assert payload["name"] == "DEMO TOPIC"

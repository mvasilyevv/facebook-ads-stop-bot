# -*- coding: utf-8 -*-
"""Тесты Telegram Bot API клиента."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from core.telegram.client import TelegramAPIError, TelegramBotClient
from core.telegram.messaging import safe_edit_or_send_message


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


# Проверяем, что HTTP 400 с JSON Telegram превращается в TelegramAPIError с описанием.
@pytest.mark.asyncio
async def test_edit_message_raises_telegram_api_error_from_telegram_json():
    http_client = AsyncMock()
    response = MagicMock()
    response.status_code = 400
    response.json.return_value = {
        "ok": False,
        "error_code": 400,
        "description": "Bad Request: message is not modified",
    }
    response.raise_for_status.side_effect = AssertionError("raise_for_status не нужен")
    http_client.post = AsyncMock(return_value=response)

    client = TelegramBotClient("secret-token", http_client=http_client)

    with pytest.raises(TelegramAPIError) as exc_info:
        await client.edit_message(chat_id="-100123", message_id=10, text="Тест")

    assert exc_info.value.method == "editMessageText"
    assert exc_info.value.error_code == 400
    assert exc_info.value.description == "Bad Request: message is not modified"
    assert exc_info.value.__cause__ is None


# Проверяем, что повторное редактирование тем же текстом не шумит ошибкой.
@pytest.mark.asyncio
async def test_safe_edit_or_send_message_ignores_not_modified_edit():
    http_client = AsyncMock()
    response = MagicMock()
    response.status_code = 400
    response.json.return_value = {
        "ok": False,
        "error_code": 400,
        "description": "Bad Request: message is not modified",
    }
    http_client.post = AsyncMock(return_value=response)

    client = TelegramBotClient("secret-token", http_client=http_client)
    action, message_id = await safe_edit_or_send_message(
        client,
        chat_id="-100123",
        message_id=10,
        text="Тест",
    )

    assert (action, message_id) == ("unchanged", 10)
    assert http_client.post.await_count == 1


# Проверяем, что устаревший message_id заменяется отправкой нового сообщения.
@pytest.mark.asyncio
async def test_safe_edit_or_send_message_sends_new_message_when_edit_target_missing():
    http_client = AsyncMock()
    edit_response = MagicMock()
    edit_response.status_code = 400
    edit_response.json.return_value = {
        "ok": False,
        "error_code": 400,
        "description": "Bad Request: message to edit not found",
    }
    send_response = MagicMock()
    send_response.status_code = 200
    send_response.json.return_value = {"ok": True, "result": {"message_id": 77}}
    http_client.post = AsyncMock(side_effect=[edit_response, send_response])

    client = TelegramBotClient("secret-token", http_client=http_client)
    action, message_id = await safe_edit_or_send_message(
        client,
        chat_id="-100123",
        message_id=10,
        text="Тест",
    )

    assert (action, message_id) == ("sent", 77)
    assert http_client.post.await_args_list[0].args[0].endswith("/editMessageText")
    assert http_client.post.await_args_list[1].args[0].endswith("/sendMessage")


# Проверяем, что транспортная ошибка не протаскивает URL с токеном в cause traceback.
@pytest.mark.asyncio
async def test_transport_error_does_not_keep_httpx_cause_with_token_url():
    http_client = AsyncMock()
    request = httpx.Request("POST", "https://api.telegram.org/botsecret-token/sendMessage")
    http_client.post = AsyncMock(side_effect=httpx.ConnectError("boom", request=request))

    client = TelegramBotClient("secret-token", http_client=http_client)

    with pytest.raises(RuntimeError) as exc_info:
        await client.send_message(chat_id="-100123", text="Тест")

    assert str(exc_info.value) == "Не удалось выполнить запрос к Telegram API (sendMessage)"
    assert exc_info.value.__cause__ is None

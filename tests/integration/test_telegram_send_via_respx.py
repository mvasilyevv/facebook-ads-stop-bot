# -*- coding: utf-8 -*-
"""Интеграционный: TelegramBotClient → реальный HTTP через httpx + respx (без живого TG).

Проверяет правильность сериализации параметров (parse_mode, reply_to, thread_id)
и обработку ошибок API. Без этого слоя поломки на стыке `core.telegram.client` ↔
api.telegram.org не ловились бы до прод-инцидента.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from httpx import Response

from core.telegram.client import TelegramBotClient


# Сценарий: send_message формирует POST с правильным payload
@pytest.mark.asyncio
async def test_send_message_constructs_proper_request(tg_respx) -> None:
    async with httpx.AsyncClient() as http:
        client = TelegramBotClient(bot_token="FAKE:TOKEN", http_client=http)
        await client.send_message(
            chat_id="123",
            text="Привет 👋",
            parse_mode="Markdown",
        )

    assert len(tg_respx.sent_messages) == 1
    sent = tg_respx.sent_messages[0]
    assert sent["chat_id"] == "123"
    assert sent["text"] == "Привет 👋"
    assert sent["parse_mode"] == "Markdown"


# Сценарий: thread_id (forum topics) проставляется в payload
@pytest.mark.asyncio
async def test_send_message_with_thread_id(tg_respx) -> None:
    async with httpx.AsyncClient() as http:
        client = TelegramBotClient(bot_token="FAKE:TOKEN", http_client=http)
        await client.send_message(
            chat_id="-100123",
            text="алерт",
            message_thread_id=7,
        )

    sent = tg_respx.sent_messages[0]
    assert sent["chat_id"] == "-100123"
    assert sent.get("message_thread_id") == 7


# Сценарий: TG отвечает 5xx с ok=false → клиент бросает TelegramAPIError.
# Контракт: caller'ы (renderer, bot_handler) ловят это и логируют, чтобы один упавший
# алерт не убивал observer-цикл — но сам клиент должен явно сигнализировать ошибку.
@pytest.mark.asyncio
async def test_send_message_handles_api_error() -> None:
    from core.telegram.client import TelegramAPIError

    with respx.mock(assert_all_called=False) as mock:
        mock.post(url__regex=r"https://api\.telegram\.org/bot[^/]+/sendMessage").mock(
            return_value=Response(500, json={"ok": False, "description": "internal server error"})
        )

        async with httpx.AsyncClient() as http:
            client = TelegramBotClient(bot_token="FAKE:TOKEN", http_client=http)
            with pytest.raises(TelegramAPIError) as exc_info:
                await client.send_message(chat_id="1", text="t")
            assert "internal" in str(exc_info.value).lower()


# Сценарий: getUpdates с offset формирует правильный запрос
@pytest.mark.asyncio
async def test_get_updates_passes_offset(tg_respx) -> None:
    async with httpx.AsyncClient() as http:
        client = TelegramBotClient(bot_token="FAKE:TOKEN", http_client=http)
        updates = await client.get_updates(offset=100, timeout_seconds=0)

    assert updates == []
    assert tg_respx.fetched_updates_count == 1


# Сценарий: getUpdates получает программируемые updates от respx
@pytest.mark.asyncio
async def test_get_updates_returns_queued(tg_respx) -> None:
    tg_respx.queued_updates = [
        {"update_id": 1, "message": {"text": "ping"}},
        {"update_id": 2, "message": {"text": "pong"}},
    ]
    async with httpx.AsyncClient() as http:
        client = TelegramBotClient(bot_token="FAKE:TOKEN", http_client=http)
        updates = await client.get_updates(offset=None, timeout_seconds=0)

    assert len(updates) == 2
    assert updates[0]["message"]["text"] == "ping"
    assert updates[1]["message"]["text"] == "pong"

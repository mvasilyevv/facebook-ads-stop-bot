# -*- coding: utf-8 -*-
"""Тесты методов set_my_commands и set_chat_menu_button клиента Telegram Bot API."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.telegram.client import TelegramBotClient


def _make_client_with_mock():
    """Возвращает (client, http_mock) с заглушкой httpx-клиента."""
    http_client = AsyncMock()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"ok": True, "result": True}
    response.is_error = False
    http_client.post = AsyncMock(return_value=response)
    client = TelegramBotClient("test-token", http_client=http_client)
    return client, http_client


# Проверяем, что set_my_commands отправляет payload с полем commands.
@pytest.mark.asyncio
async def test_set_my_commands_sends_correct_payload():
    client, http_client = _make_client_with_mock()
    commands = [
        {"command": "start", "description": "Запустить бота"},
        {"command": "status", "description": "Статус"},
    ]
    try:
        await client.set_my_commands(commands)
    finally:
        await client.close()

    assert http_client.post.called
    call_url: str = http_client.post.await_args.args[0]
    assert "setMyCommands" in call_url
    payload = http_client.post.await_args.kwargs["json"]
    assert payload["commands"] == commands


# Проверяем, что set_chat_menu_button отправляет menu_button с type=web_app.
@pytest.mark.asyncio
async def test_set_chat_menu_button_sends_web_app_payload():
    client, http_client = _make_client_with_mock()
    try:
        await client.set_chat_menu_button(web_app_url="https://example.com/app")
    finally:
        await client.close()

    assert http_client.post.called
    call_url: str = http_client.post.await_args.args[0]
    assert "setChatMenuButton" in call_url
    payload = http_client.post.await_args.kwargs["json"]
    menu_button = payload["menu_button"]
    assert menu_button["type"] == "web_app"
    assert menu_button["web_app"]["url"] == "https://example.com/app"
    assert "text" in menu_button


# Проверяем, что set_chat_menu_button с http:// (не https) поднимает ValueError.
@pytest.mark.asyncio
async def test_set_chat_menu_button_raises_on_http_url():
    client, _ = _make_client_with_mock()
    try:
        with pytest.raises(ValueError, match="HTTPS"):
            await client.set_chat_menu_button(web_app_url="http://example.com/app")
    finally:
        await client.close()

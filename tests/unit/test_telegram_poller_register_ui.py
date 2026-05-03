# -*- coding: utf-8 -*-
"""Тесты для функции _register_bot_ui в telegram poller."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.telegram_poller.main import _register_bot_ui


def _make_client() -> MagicMock:
    client = MagicMock()
    client.set_my_commands = AsyncMock()
    client.set_chat_menu_button = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_register_bot_ui_calls_set_my_commands():
    # Проверяем что set_my_commands вызывается для всех scope (default, private, group, admins)
    client = _make_client()
    with patch(
        "apps.telegram_poller.main.load_web_app_url", new_callable=AsyncMock, return_value=""
    ):
        await _register_bot_ui(client)

    assert client.set_my_commands.call_count == 4
    expected_scopes = {
        "default",
        "all_private_chats",
        "all_group_chats",
        "all_chat_administrators",
    }
    actual_scopes = {call.kwargs["scope"]["type"] for call in client.set_my_commands.call_args_list}
    assert actual_scopes == expected_scopes
    commands = client.set_my_commands.call_args_list[0].args[0]
    assert len(commands) == 3
    assert any(c["command"] == "start" for c in commands)
    assert any(c["command"] == "app" for c in commands)
    assert any(c["command"] == "help" for c in commands)


@pytest.mark.asyncio
async def test_register_bot_ui_no_web_app_url_skips_menu_button(caplog):
    # Если WEB_APP_URL пустой — set_chat_menu_button не вызывается и есть warning
    client = _make_client()
    with patch(
        "apps.telegram_poller.main.load_web_app_url", new_callable=AsyncMock, return_value=""
    ):
        with caplog.at_level("WARNING"):
            await _register_bot_ui(client)

    client.set_chat_menu_button.assert_not_called()
    assert "WEB_APP_URL не задан" in caplog.text


@pytest.mark.asyncio
async def test_register_bot_ui_http_url_skips_menu_button(caplog):
    # Если WEB_APP_URL начинается с http:// — set_chat_menu_button не вызывается
    client = _make_client()
    with patch(
        "apps.telegram_poller.main.load_web_app_url",
        new_callable=AsyncMock,
        return_value="http://example.com/app",
    ):
        with caplog.at_level("WARNING"):
            await _register_bot_ui(client)

    client.set_chat_menu_button.assert_not_called()
    assert "WEB_APP_URL не https" in caplog.text


@pytest.mark.asyncio
async def test_register_bot_ui_valid_url_calls_menu_button():
    # Если WEB_APP_URL корректный https — set_chat_menu_button вызывается с правильным url
    client = _make_client()
    url = "https://example.com/app"
    with patch(
        "apps.telegram_poller.main.load_web_app_url",
        new_callable=AsyncMock,
        return_value=url,
    ):
        await _register_bot_ui(client)

    client.set_chat_menu_button.assert_called_once_with(web_app_url=url)


@pytest.mark.asyncio
async def test_register_bot_ui_returns_url_on_success():
    # _register_bot_ui возвращает URL, который был установлен
    client = _make_client()
    url = "https://example.com/app"
    with patch(
        "apps.telegram_poller.main.load_web_app_url",
        new_callable=AsyncMock,
        return_value=url,
    ):
        result = await _register_bot_ui(client)

    assert result == url


@pytest.mark.asyncio
async def test_register_bot_ui_returns_empty_string_when_no_url():
    # _register_bot_ui возвращает пустую строку если URL не задан
    client = _make_client()
    with patch(
        "apps.telegram_poller.main.load_web_app_url", new_callable=AsyncMock, return_value=""
    ):
        result = await _register_bot_ui(client)

    assert result == ""

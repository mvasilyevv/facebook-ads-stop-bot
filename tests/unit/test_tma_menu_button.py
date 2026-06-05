# -*- coding: utf-8 -*-
"""Unit-тесты авто-установки Telegram Menu Button (точка входа Mini App).

Quick-tunnel меняет URL при каждом запуске → при сохранении свежего web_app_url
бот должен сам прописать Menu Button (setChatMenuButton), иначе кнопка mini-app
остаётся на мёртвом старом туннеле. Best-effort — ошибки не валят сохранение URL.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import apps.api.routers.v1.settings_telegram as st
import core.telegram.client as cli
import core.telegram.service as svc


def _fake_client() -> AsyncMock:
    c = AsyncMock()
    c.set_chat_menu_button = AsyncMock()
    c.close = AsyncMock()
    return c


# Бот настроен → set_chat_menu_button вызван с переданным URL, возвращает True
@pytest.mark.asyncio
async def test_sets_menu_button_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        svc, "load_telegram_config", AsyncMock(return_value=SimpleNamespace(bot_token="123:ABC"))
    )
    client = _fake_client()
    monkeypatch.setattr(cli, "TelegramBotClient", lambda **_kw: client)

    url = "https://fresh.trycloudflare.com/tma/"
    ok = await st._sync_bot_menu_button(object(), url)

    assert ok is True
    client.set_chat_menu_button.assert_awaited_once()
    assert client.set_chat_menu_button.call_args.kwargs["web_app_url"] == url
    client.close.assert_awaited_once()


# Бот не настроен (нет конфига) → skip, кнопка не ставится, клиент не создаётся
@pytest.mark.asyncio
async def test_skips_when_no_config(monkeypatch) -> None:
    monkeypatch.setattr(svc, "load_telegram_config", AsyncMock(return_value=None))
    spy_ctor = AsyncMock()
    monkeypatch.setattr(cli, "TelegramBotClient", spy_ctor)

    ok = await st._sync_bot_menu_button(object(), "https://x.trycloudflare.com/tma/")

    assert ok is False
    spy_ctor.assert_not_called()


# Конфиг без bot_token → skip
@pytest.mark.asyncio
async def test_skips_when_no_token(monkeypatch) -> None:
    monkeypatch.setattr(
        svc, "load_telegram_config", AsyncMock(return_value=SimpleNamespace(bot_token=""))
    )
    spy_ctor = AsyncMock()
    monkeypatch.setattr(cli, "TelegramBotClient", spy_ctor)

    ok = await st._sync_bot_menu_button(object(), "https://x.trycloudflare.com/tma/")

    assert ok is False
    spy_ctor.assert_not_called()


# Ошибка Telegram при установке → False (не падает), клиент закрыт
@pytest.mark.asyncio
async def test_returns_false_on_telegram_error(monkeypatch) -> None:
    monkeypatch.setattr(
        svc, "load_telegram_config", AsyncMock(return_value=SimpleNamespace(bot_token="123:ABC"))
    )
    client = _fake_client()
    client.set_chat_menu_button = AsyncMock(side_effect=RuntimeError("tg down"))
    monkeypatch.setattr(cli, "TelegramBotClient", lambda **_kw: client)

    ok = await st._sync_bot_menu_button(object(), "https://x.trycloudflare.com/tma/")

    assert ok is False
    client.close.assert_awaited_once()

# -*- coding: utf-8 -*-
"""Unit-тесты авто-установки Telegram Menu Button (точка входа Mini App).

Quick-tunnel меняет URL при каждом запуске → при сохранении свежего web_app_url
бот должен сам прописать Menu Button (setChatMenuButton), иначе кнопка mini-app
остаётся на мёртвом старом туннеле. Ошибки дают явный incomplete result.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import apps.api.routers.v1.settings_telegram as st
import core.telegram.gateway as gateway
import core.telegram.menu_button as menu_button
import core.telegram.outbound_authority as outbound_authority
import core.telegram.service as svc


class _Authority:
    def __init__(self, authorized: bool = True) -> None:
        self.authorized = authorized

    async def __aenter__(self) -> bool:
        return self.authorized

    async def __aexit__(self, *_args) -> None:
        return None


def _fake_client() -> AsyncMock:
    c = AsyncMock()
    c.credential_fingerprint = "0" * 64
    c.set_chat_menu_button = AsyncMock()
    c.close = AsyncMock()
    return c


# Бот настроен → set_chat_menu_button вызван с переданным URL, возвращает True
@pytest.mark.asyncio
async def test_sets_menu_button_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        svc,
        "load_telegram_config",
        AsyncMock(return_value=SimpleNamespace(bot_token="123:ABC", webhook_generation=7)),
    )
    monkeypatch.setattr(
        outbound_authority,
        "hold_telegram_outbound_authority",
        lambda *_args, **_kwargs: _Authority(),
    )
    client = _fake_client()
    monkeypatch.setattr(gateway, "TelegramHTMLGateway", lambda **_kw: client)
    monkeypatch.setattr(
        menu_button,
        "load_active_recipients",
        AsyncMock(return_value=[SimpleNamespace(chat_id=123)]),
    )

    url = "https://fresh.trycloudflare.com/tma/"
    ok = await st._sync_bot_menu_button(object(), url)

    assert ok is True
    assert client.set_chat_menu_button.await_count == 2
    default_call, private_call = client.set_chat_menu_button.await_args_list
    assert default_call.kwargs == {"web_app_url": url}
    assert private_call.kwargs == {"web_app_url": url, "chat_id": 123}
    client.close.assert_awaited_once()


# Бот не настроен (нет конфига) → skip, кнопка не ставится, клиент не создаётся
@pytest.mark.asyncio
async def test_skips_when_no_config(monkeypatch) -> None:
    monkeypatch.setattr(svc, "load_telegram_config", AsyncMock(return_value=None))
    spy_ctor = AsyncMock()
    monkeypatch.setattr(gateway, "TelegramHTMLGateway", spy_ctor)

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
    monkeypatch.setattr(gateway, "TelegramHTMLGateway", spy_ctor)

    ok = await st._sync_bot_menu_button(object(), "https://x.trycloudflare.com/tma/")

    assert ok is False
    spy_ctor.assert_not_called()


# Ошибка Telegram при установке → False (не падает), клиент закрыт
@pytest.mark.asyncio
async def test_returns_false_on_telegram_error(monkeypatch) -> None:
    monkeypatch.setattr(
        svc,
        "load_telegram_config",
        AsyncMock(return_value=SimpleNamespace(bot_token="123:ABC", webhook_generation=7)),
    )
    monkeypatch.setattr(
        outbound_authority,
        "hold_telegram_outbound_authority",
        lambda *_args, **_kwargs: _Authority(),
    )
    client = _fake_client()
    client.set_chat_menu_button = AsyncMock(side_effect=RuntimeError("tg down"))
    monkeypatch.setattr(gateway, "TelegramHTMLGateway", lambda **_kw: client)

    ok = await st._sync_bot_menu_button(object(), "https://x.trycloudflare.com/tma/")

    assert ok is False
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_authority_rejection_makes_zero_menu_button_calls(monkeypatch) -> None:
    monkeypatch.setattr(
        svc,
        "load_telegram_config",
        AsyncMock(return_value=SimpleNamespace(bot_token="123:ABC", webhook_generation=7)),
    )
    client = _fake_client()
    monkeypatch.setattr(gateway, "TelegramHTMLGateway", lambda **_kw: client)
    monkeypatch.setattr(
        outbound_authority,
        "hold_telegram_outbound_authority",
        lambda *_args, **_kwargs: _Authority(False),
    )

    ok = await st._sync_bot_menu_button(
        object(),
        "https://x.trycloudflare.com/tma/",
    )

    assert ok is False
    client.set_chat_menu_button.assert_not_awaited()
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_authority_is_refenced_before_each_scope(monkeypatch) -> None:
    monkeypatch.setattr(
        svc,
        "load_telegram_config",
        AsyncMock(return_value=SimpleNamespace(bot_token="123:ABC", webhook_generation=7)),
    )
    permits = iter([True, False])
    monkeypatch.setattr(
        outbound_authority,
        "hold_telegram_outbound_authority",
        lambda *_args, **_kwargs: _Authority(next(permits)),
    )
    client = _fake_client()
    monkeypatch.setattr(gateway, "TelegramHTMLGateway", lambda **_kw: client)
    monkeypatch.setattr(
        menu_button,
        "load_active_recipients",
        AsyncMock(return_value=[SimpleNamespace(chat_id=123), SimpleNamespace(chat_id=456)]),
    )

    ok = await st._sync_bot_menu_button(object(), "https://operator.example/tma/")

    assert ok is False
    client.set_chat_menu_button.assert_awaited_once_with(
        web_app_url="https://operator.example/tma/"
    )

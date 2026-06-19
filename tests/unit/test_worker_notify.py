# -*- coding: utf-8 -*-
"""Unit-тесты worker_notify: best-effort DM owner'ам с dedup ПОСЛЕ отправки."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import core.telegram.worker_notify as wn
from core.telegram.service import Recipient


def _owner(chat_id=111):
    return Recipient(chat_id=chat_id, telegram_user_id=1, username="u", role="owner")


def _cfg():
    return SimpleNamespace(bot_token="T", chat_id=None)


@pytest.fixture(autouse=True)
def _clear_client_cache():
    wn._reset_client_cache()
    yield
    wn._reset_client_cache()


# Нет owner-получателей → no-op, возвращает False, dedup не ставится
@pytest.mark.asyncio
async def test_no_recipients_returns_false(monkeypatch):
    monkeypatch.setattr(wn, "load_telegram_config", AsyncMock(return_value=_cfg()))
    monkeypatch.setattr(wn, "load_owner_recipients", AsyncMock(return_value=[]))
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    sent = await wn.notify_owners(
        object(), redis, category="x", text="t", dedup_key="k", dedup_ttl_seconds=60
    )
    assert sent is False
    redis.set.assert_not_awaited()


# Успех доставки → True, dedup ставится ПОСЛЕ отправки (SET с nx+ex)
@pytest.mark.asyncio
async def test_success_sets_dedup_after_send(monkeypatch):
    monkeypatch.setattr(wn, "load_telegram_config", AsyncMock(return_value=_cfg()))
    monkeypatch.setattr(wn, "load_owner_recipients", AsyncMock(return_value=[_owner()]))
    client = AsyncMock()
    monkeypatch.setattr(wn, "_client_for_token", lambda tok: client)
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    sent = await wn.notify_owners(
        object(), redis, category="x", text="t", dedup_key="k", dedup_ttl_seconds=60
    )
    assert sent is True
    client.send_message.assert_awaited_once()
    assert client.send_message.await_args.kwargs["chat_id"] == "111"
    redis.set.assert_awaited_once()
    assert redis.set.await_args.kwargs.get("nx") is True
    assert redis.set.await_args.kwargs.get("ex") == 60


# Отправка упала → dedup НЕ ставится (чтобы ретрайнуть позже), возвращает False
@pytest.mark.asyncio
async def test_send_failure_keeps_dedup_unset(monkeypatch):
    monkeypatch.setattr(wn, "load_telegram_config", AsyncMock(return_value=_cfg()))
    monkeypatch.setattr(wn, "load_owner_recipients", AsyncMock(return_value=[_owner()]))
    client = AsyncMock()
    client.send_message = AsyncMock(side_effect=RuntimeError("tg down"))
    monkeypatch.setattr(wn, "_client_for_token", lambda tok: client)
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    sent = await wn.notify_owners(
        object(), redis, category="x", text="t", dedup_key="k", dedup_ttl_seconds=60
    )
    assert sent is False
    redis.set.assert_not_awaited()


# dedup уже стоит → ранний выход, отправки нет
@pytest.mark.asyncio
async def test_dedup_already_set_skips(monkeypatch):
    monkeypatch.setattr(wn, "load_telegram_config", AsyncMock(return_value=_cfg()))
    lor = AsyncMock(return_value=[_owner()])
    monkeypatch.setattr(wn, "load_owner_recipients", lor)
    redis = AsyncMock()
    redis.get = AsyncMock(return_value="1")
    sent = await wn.notify_owners(
        object(), redis, category="x", text="t", dedup_key="k", dedup_ttl_seconds=60
    )
    assert sent is False
    lor.assert_not_awaited()


# Нет токена в конфиге → no-op False (не падает)
@pytest.mark.asyncio
async def test_no_token_returns_false(monkeypatch):
    monkeypatch.setattr(wn, "load_telegram_config", AsyncMock(return_value=None))
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    sent = await wn.notify_owners(object(), redis, category="x", text="t")
    assert sent is False

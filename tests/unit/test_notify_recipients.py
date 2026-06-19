# -*- coding: utf-8 -*-
"""Unit-тесты notify_recipients: рассылка ВСЕМ активным recipients, dedup-after-send."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import core.telegram.worker_notify as wn
from core.telegram.service import Recipient


def _r(chat_id, role="recipient"):
    return Recipient(chat_id=chat_id, telegram_user_id=chat_id, username="u", role=role)


def _cfg():
    return SimpleNamespace(bot_token="T", chat_id=None)


@pytest.fixture(autouse=True)
def _clear():
    wn._reset_client_cache()
    yield
    wn._reset_client_cache()


# Рассылка двум recipients → 2 send, True, dedup ставится после
@pytest.mark.asyncio
async def test_broadcasts_to_all(monkeypatch):
    monkeypatch.setattr(wn, "load_telegram_config", AsyncMock(return_value=_cfg()))
    monkeypatch.setattr(
        wn, "load_active_recipients", AsyncMock(return_value=[_r(111, "owner"), _r(222)])
    )
    client = AsyncMock()
    monkeypatch.setattr(wn, "_client_for_token", lambda t: client)
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    sent = await wn.notify_recipients(
        object(), redis, category="x", text="t", dedup_key="k", dedup_ttl_seconds=60
    )
    assert sent is True
    assert client.send_message.await_count == 2
    chats = {c.kwargs["chat_id"] for c in client.send_message.await_args_list}
    assert chats == {"111", "222"}
    redis.set.assert_awaited_once()


# Нет recipients → False, без отправки и dedup
@pytest.mark.asyncio
async def test_no_recipients_false(monkeypatch):
    monkeypatch.setattr(wn, "load_telegram_config", AsyncMock(return_value=_cfg()))
    monkeypatch.setattr(wn, "load_active_recipients", AsyncMock(return_value=[]))
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    sent = await wn.notify_recipients(
        object(), redis, category="x", text="t", dedup_key="k", dedup_ttl_seconds=60
    )
    assert sent is False
    redis.set.assert_not_awaited()


# Частичный сбой (один send падает) → True (доставлено ≥1), dedup ставится
@pytest.mark.asyncio
async def test_partial_failure_still_true(monkeypatch):
    monkeypatch.setattr(wn, "load_telegram_config", AsyncMock(return_value=_cfg()))
    monkeypatch.setattr(wn, "load_active_recipients", AsyncMock(return_value=[_r(111), _r(222)]))
    client = AsyncMock()
    client.send_message = AsyncMock(side_effect=[RuntimeError("x"), {"ok": True}])
    monkeypatch.setattr(wn, "_client_for_token", lambda t: client)
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    sent = await wn.notify_recipients(
        object(), redis, category="x", text="t", dedup_key="k", dedup_ttl_seconds=60
    )
    assert sent is True
    redis.set.assert_awaited_once()

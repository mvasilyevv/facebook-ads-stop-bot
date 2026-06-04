# -*- coding: utf-8 -*-
"""Unit-тесты _send_alert_with_fallback: доставка алерта с fallback в General.

Money-контракт: алерт (особенно warning) не теряется, если форум-топик удалён/закрыт —
dispatcher пересылает его в общий чат без thread_id.
"""

from __future__ import annotations

import pytest

from core.telegram.alert_dispatcher import _send_alert_with_fallback
from core.telegram.client import TelegramAPIError


class _FakeClient:
    """Фейковый TG-клиент: поведение каждого send_message задаётся списком."""

    def __init__(self, behavior: list[str]) -> None:
        self.behavior = behavior  # "ok" | "thread_err" | "other_err"
        self.calls: list[int | None] = []

    async def send_message(self, *, chat_id, text, parse_mode, message_thread_id, reply_markup):
        self.calls.append(message_thread_id)
        action = self.behavior[len(self.calls) - 1]
        if action == "ok":
            return {"message_id": 555}
        if action == "thread_err":
            raise TelegramAPIError(method="sendMessage", description="message thread not found")
        raise TelegramAPIError(method="sendMessage", description="bad request: something else")


async def _run(client, thread_id):
    return await _send_alert_with_fallback(
        client,
        chat_id="-100123",
        text_msg="текст",
        keyboard=None,
        thread_id=thread_id,
        event_id=1,
    )


# Успешная отправка в топик → ответ, один вызов (без fallback).
@pytest.mark.asyncio
async def test_send_ok_no_fallback():
    client = _FakeClient(["ok"])
    res = await _run(client, 1023)
    assert res == {"message_id": 555}
    assert client.calls == [1023]


# Топик недоступен (thread error) → fallback в General (thread_id=None), успех.
@pytest.mark.asyncio
async def test_thread_error_fallback_to_general():
    client = _FakeClient(["thread_err", "ok"])
    res = await _run(client, 1023)
    assert res == {"message_id": 555}
    assert client.calls == [1023, None]  # сначала топик, затем General


# Не-thread ошибка → НЕ fallback, возвращает None (один вызов).
@pytest.mark.asyncio
async def test_other_error_no_fallback():
    client = _FakeClient(["other_err"])
    res = await _run(client, 1023)
    assert res is None
    assert client.calls == [1023]


# Топик недоступен И General тоже падает → None (оба вызова были).
@pytest.mark.asyncio
async def test_thread_error_and_general_fails():
    client = _FakeClient(["thread_err", "other_err"])
    res = await _run(client, 1023)
    assert res is None
    assert client.calls == [1023, None]


# thread_id=None (топик не настроен) + ошибка → НЕ fallback (повторять некуда).
@pytest.mark.asyncio
async def test_no_thread_id_no_fallback():
    client = _FakeClient(["thread_err"])
    res = await _run(client, None)
    assert res is None
    assert client.calls == [None]

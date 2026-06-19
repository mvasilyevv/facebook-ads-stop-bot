# -*- coding: utf-8 -*-
"""TelegramBotClient ретраит 503 (1 повтор), затем возвращает успешный ответ."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from core.telegram.client import TelegramBotClient


# 503 затем 200 → один ретрай, итог 200
@pytest.mark.asyncio
async def test_retries_on_503(monkeypatch):
    client = TelegramBotClient("T")
    calls = []

    async def fake_post(url, json, **_kw):
        calls.append(1)
        status = 503 if len(calls) == 1 else 200
        return httpx.Response(status, json={"ok": status == 200, "result": {}})

    monkeypatch.setattr(client._http, "post", fake_post)
    monkeypatch.setattr("asyncio.sleep", AsyncMock())  # не ждать реально
    resp = await client._do_request("sendMessage", payload={"chat_id": "1", "text": "x"})
    assert resp.status_code == 200
    assert len(calls) == 2


# 502 три раза подряд → исчерпаны все ретраи, возвращает последний 502
@pytest.mark.asyncio
async def test_exhausts_retries_on_persistent_502(monkeypatch):
    client = TelegramBotClient("T")
    calls = []

    async def fake_post(url, json, **_kw):
        calls.append(1)
        return httpx.Response(502, json={"ok": False})

    monkeypatch.setattr(client._http, "post", fake_post)
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    resp = await client._do_request("sendMessage", payload={"chat_id": "1", "text": "x"})
    # исходный вызов + 2 ретрая = 3 всего
    assert resp.status_code == 502
    assert len(calls) == 3


# 504 → 503 → 200: два ретрая, итог 200
@pytest.mark.asyncio
async def test_recovers_on_second_retry(monkeypatch):
    client = TelegramBotClient("T")
    responses = [504, 503, 200]
    calls = []

    async def fake_post(url, json, **_kw):
        status = responses[len(calls)]
        calls.append(1)
        return httpx.Response(status, json={"ok": status == 200, "result": {}})

    monkeypatch.setattr(client._http, "post", fake_post)
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    resp = await client._do_request("sendMessage", payload={"chat_id": "1", "text": "x"})
    assert resp.status_code == 200
    assert len(calls) == 3


# 429 продолжает работать независимо — ретрай 5xx не ломает 429-ветку
@pytest.mark.asyncio
async def test_429_still_works(monkeypatch):
    client = TelegramBotClient("T")
    calls = []

    async def fake_post(url, json, **_kw):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"parameters": {"retry_after": 1}})
        return httpx.Response(200, json={"ok": True, "result": {}})

    monkeypatch.setattr(client._http, "post", fake_post)
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    resp = await client._do_request("sendMessage", payload={"chat_id": "1", "text": "x"})
    assert resp.status_code == 200
    assert len(calls) == 2


# 200 с первого раза — никаких дополнительных вызовов
@pytest.mark.asyncio
async def test_no_retry_on_200(monkeypatch):
    client = TelegramBotClient("T")
    calls = []

    async def fake_post(url, json, **_kw):
        calls.append(1)
        return httpx.Response(200, json={"ok": True, "result": {}})

    monkeypatch.setattr(client._http, "post", fake_post)
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    resp = await client._do_request("sendMessage", payload={"chat_id": "1", "text": "x"})
    assert resp.status_code == 200
    assert len(calls) == 1

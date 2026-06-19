# -*- coding: utf-8 -*-
"""health_watchdog: при сбое TG dedup-ключ НЕ ставится (алерт не теряется на TTL)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import apps.health_watchdog.main as hw


# Отправка упала → SET NX не вызывается, возвращает False
@pytest.mark.asyncio
async def test_send_fail_no_dedup(monkeypatch):
    monkeypatch.setattr(hw, "_send_alert", AsyncMock(return_value=False))
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)  # дедуп не стоит
    redis.set = AsyncMock(return_value=True)
    ok = await hw._maybe_alert_with_dedup(
        redis, dedup_key="k", text="t", tg_client=object(), chat_id="1", thread_id=None
    )
    assert ok is False
    redis.set.assert_not_awaited()


# Отправка ок → SET NX ставится, возвращает True
@pytest.mark.asyncio
async def test_send_ok_sets_dedup(monkeypatch):
    monkeypatch.setattr(hw, "_send_alert", AsyncMock(return_value=True))
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)  # дедуп не стоит
    redis.set = AsyncMock(return_value=True)
    ok = await hw._maybe_alert_with_dedup(
        redis, dedup_key="k", text="t", tg_client=object(), chat_id="1", thread_id=None
    )
    assert ok is True
    redis.set.assert_awaited_once()
    assert redis.set.await_args.kwargs.get("nx") is True


# Дедуп уже стоит (GET вернул "1") → ни отправки, ни нового SET
@pytest.mark.asyncio
async def test_dedup_already_set_skips_send(monkeypatch):
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(hw, "_send_alert", send_mock)
    redis = AsyncMock()
    redis.get = AsyncMock(return_value="1")  # ключ уже стоит
    redis.set = AsyncMock(return_value=True)
    ok = await hw._maybe_alert_with_dedup(
        redis, dedup_key="k", text="t", tg_client=object(), chat_id="1", thread_id=None
    )
    assert ok is False
    send_mock.assert_not_awaited()
    redis.set.assert_not_awaited()

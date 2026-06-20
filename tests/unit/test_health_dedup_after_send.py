# -*- coding: utf-8 -*-
"""health_watchdog: при сбое TG dedup-ключ НЕ ставится (алерт не теряется на TTL)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import apps.health_watchdog.main as hw


# Отправка упала → SET NX не вызывается, возвращает False
@pytest.mark.asyncio
async def test_send_fail_no_dedup(monkeypatch):
    monkeypatch.setattr(hw, "notify_recipients", AsyncMock(return_value=False))
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)  # дедуп не стоит
    redis.set = AsyncMock(return_value=True)
    engine = object()
    ok = await hw._maybe_alert_with_dedup(redis, dedup_key="k", text="t", engine=engine)
    assert ok is False
    redis.set.assert_not_awaited()


# Отправка ок → SET NX ставится, возвращает True
@pytest.mark.asyncio
async def test_send_ok_sets_dedup(monkeypatch):
    monkeypatch.setattr(hw, "notify_recipients", AsyncMock(return_value=True))
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)  # дедуп не стоит
    redis.set = AsyncMock(return_value=True)
    engine = object()
    ok = await hw._maybe_alert_with_dedup(redis, dedup_key="k", text="t", engine=engine)
    assert ok is True
    redis.set.assert_awaited_once()
    assert redis.set.await_args.kwargs.get("nx") is True


# Дедуп уже стоит (GET вернул "1") → ни отправки, ни нового SET
@pytest.mark.asyncio
async def test_dedup_already_set_skips_send(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(hw, "notify_recipients", spy)
    redis = AsyncMock()
    redis.get = AsyncMock(return_value="1")  # ключ уже стоит
    redis.set = AsyncMock(return_value=True)
    engine = object()
    ok = await hw._maybe_alert_with_dedup(redis, dedup_key="k", text="t", engine=engine)
    assert ok is False
    spy.assert_not_awaited()
    redis.set.assert_not_awaited()

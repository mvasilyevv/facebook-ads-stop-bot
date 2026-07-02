# -*- coding: utf-8 -*-
"""Observer degraded-алерт (Layer 3): доставка через telegram_recipients (инцидент 01.07).

Легаси-путь слал напрямую в telegram_config.chat_id (NULL в проде) и молча
``return False`` — при слепом канале владелец не получил ни одного degraded-алерта,
хотя детект отработал трижды. Новый контракт: доставка через notify_recipients
(тот же путь, что health_watchdog), warning при недоставке, сброс дедупа при
недоставке (алерт не теряется на TTL).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import apps.observer_worker.main as ow


# Дедуп свободен + notify_recipients доставил → True, текст содержит суть деградации
@pytest.mark.asyncio
async def test_degraded_alert_delivers_via_recipients(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(ow, "notify_recipients", spy)
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)  # SET NX прошёл — окно свободно

    ok = await ow._maybe_alert_degraded(
        object(),
        redis,
        consecutive_failures=44,
        last_error="AioRpcError: профиль не запущен",
    )

    assert ok is True
    spy.assert_awaited_once()
    text = spy.await_args.kwargs["text"]
    assert "деградация" in text.lower()
    assert "44" in text
    # Дедуп при успехе НЕ снимается
    redis.delete.assert_not_awaited()


# Дедуп уже стоит (SET NX вернул falsy) → notify_recipients не зовётся, False
@pytest.mark.asyncio
async def test_degraded_alert_dedup_skips_send(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(ow, "notify_recipients", spy)
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=None)  # NX: ключ уже существует

    ok = await ow._maybe_alert_degraded(object(), redis, consecutive_failures=5, last_error=None)

    assert ok is False
    spy.assert_not_awaited()


# notify_recipients вернул False → warning в лог + сброс дедупа (ретрай на след. цикле)
@pytest.mark.asyncio
async def test_degraded_alert_undelivered_warns_and_rearms(monkeypatch, caplog):
    spy = AsyncMock(return_value=False)
    monkeypatch.setattr(ow, "notify_recipients", spy)
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)

    with caplog.at_level("WARNING"):
        ok = await ow._maybe_alert_degraded(
            object(), redis, consecutive_failures=7, last_error="net down"
        )

    assert ok is False
    spy.assert_awaited_once()
    # Тихий провал запрещён: обязана быть warning-строка о недоставке
    assert any("не доставлен" in r.getMessage().lower() for r in caplog.records)
    # Дедуп снят — следующий цикл деградации попробует доставить снова
    redis.delete.assert_awaited_once_with(ow.DEGRADED_ALERT_DEDUP_KEY)


# redis None (нет дедупа) → False, notify_recipients не зовётся
@pytest.mark.asyncio
async def test_degraded_alert_without_redis_noop(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(ow, "notify_recipients", spy)

    ok = await ow._maybe_alert_degraded(object(), None, consecutive_failures=3, last_error=None)

    assert ok is False
    spy.assert_not_awaited()

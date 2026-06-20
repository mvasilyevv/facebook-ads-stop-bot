# -*- coding: utf-8 -*-
"""Интеграционные тесты autostop_alert: fakeredis-счётчик подряд-фейлов + дедуп + сброс.

Сценарий: money-канал auto-stop мёртв → после N подряд сетевых фейлов ОДИН CRITICAL в TG;
успех любого auto-stop сбрасывает счётчик и снимает дедуп (re-arm на следующий outage).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.meta_api.autostop_alert import (
    AUTOSTOP_ALERT_DEDUP_KEY,
    AUTOSTOP_FAIL_COUNTER_KEY,
    maybe_alert_autostop_channel_down,
    record_autostop_success,
    register_autostop_failure_and_should_alert,
)
from core.meta_api.errors import RateLimitedError, TemporaryError


# Ниже порога — не алертим; на пороге — алертим один раз; дальше — дедуп молчит
@pytest.mark.asyncio
async def test_threshold_then_dedup(fake_redis_client) -> None:
    kw = dict(threshold=3, window_seconds=1800, dedup_ttl_seconds=1800)
    # 1-й и 2-й фейл — молчим
    assert await register_autostop_failure_and_should_alert(fake_redis_client, **kw) is False
    assert await register_autostop_failure_and_should_alert(fake_redis_client, **kw) is False
    # 3-й (порог) — алертим
    assert await register_autostop_failure_and_should_alert(fake_redis_client, **kw) is True
    # 4-й, 5-й — дедуп молчит
    assert await register_autostop_failure_and_should_alert(fake_redis_client, **kw) is False
    assert await register_autostop_failure_and_should_alert(fake_redis_client, **kw) is False


# Успех auto-stop сбрасывает счётчик И снимает дедуп → следующий outage снова алертит
@pytest.mark.asyncio
async def test_success_resets_and_rearms(fake_redis_client) -> None:
    kw = dict(threshold=2, window_seconds=1800, dedup_ttl_seconds=1800)
    assert await register_autostop_failure_and_should_alert(fake_redis_client, **kw) is False
    assert (
        await register_autostop_failure_and_should_alert(fake_redis_client, **kw) is True
    )  # alert
    # канал ожил — сброс
    await record_autostop_success(fake_redis_client)
    assert await fake_redis_client.get(AUTOSTOP_FAIL_COUNTER_KEY) is None
    assert await fake_redis_client.get(AUTOSTOP_ALERT_DEDUP_KEY) is None
    # новый outage — снова доходит до порога и алертит (re-arm сработал)
    assert await register_autostop_failure_and_should_alert(fake_redis_client, **kw) is False
    assert await register_autostop_failure_and_should_alert(fake_redis_client, **kw) is True


# Оркестрация: channel-down на пороге → один CRITICAL через notify_recipients
@pytest.mark.asyncio
async def test_orchestrator_alerts_on_channel_down(fake_redis_client) -> None:
    import core.telegram.worker_notify as wnm

    exc = TemporaryError("Failed to fetch", code=-2)
    engine = object()
    spy_notify = AsyncMock(return_value=True)
    sent_any = False
    orig = wnm.notify_recipients
    wnm.notify_recipients = spy_notify  # type: ignore[assignment]
    try:
        for _ in range(3):
            sent = await maybe_alert_autostop_channel_down(
                fake_redis_client,
                exc=exc,
                fb_ad_id="120246662749510044",
                engine=engine,
                threshold=3,
                window_seconds=1800,
                dedup_ttl_seconds=1800,
            )
            sent_any = sent_any or sent
    finally:
        wnm.notify_recipients = orig

    assert sent_any is True
    # notify_recipients вызван ровно один раз (дедуп работает)
    spy_notify.assert_awaited_once()
    kwargs = spy_notify.await_args.kwargs
    assert "120246662749510044" in kwargs["text"]


# Rate-limit (Meta-side, канал жив) НЕ инкрементит счётчик и НЕ алертит
@pytest.mark.asyncio
async def test_orchestrator_ignores_rate_limit(fake_redis_client) -> None:
    import core.telegram.worker_notify as wnm

    exc = RateLimitedError("throttled", code=4)
    engine = object()
    spy_notify = AsyncMock(return_value=True)
    orig = wnm.notify_recipients
    wnm.notify_recipients = spy_notify  # type: ignore[assignment]
    try:
        for _ in range(10):
            sent = await maybe_alert_autostop_channel_down(
                fake_redis_client,
                exc=exc,
                fb_ad_id="120246662749510044",
                engine=engine,
                threshold=3,
                window_seconds=1800,
                dedup_ttl_seconds=1800,
            )
            assert sent is False
    finally:
        wnm.notify_recipients = orig

    spy_notify.assert_not_awaited()
    # счётчик не появился — rate-limit не считается «каналом мёртв»
    assert await fake_redis_client.get(AUTOSTOP_FAIL_COUNTER_KEY) is None


# notify_recipients вернул False (нет recipients/TG не настроен) → решение True, но re-arm не блокируется
@pytest.mark.asyncio
async def test_orchestrator_no_recipients_does_not_crash(fake_redis_client) -> None:
    import core.telegram.worker_notify as wnm

    exc = TemporaryError("Failed to fetch", code=-2)
    engine = object()
    spy_notify = AsyncMock(return_value=False)
    orig = wnm.notify_recipients
    wnm.notify_recipients = spy_notify  # type: ignore[assignment]
    try:
        last = False
        for _ in range(3):
            last = await maybe_alert_autostop_channel_down(
                fake_redis_client,
                exc=exc,
                fb_ad_id="120246662749510044",
                engine=engine,
                threshold=3,
                window_seconds=1800,
                dedup_ttl_seconds=1800,
            )
    finally:
        wnm.notify_recipients = orig

    # Функция вернула True на пороге (решение принято); notify_recipients вызван 1 раз
    assert last is True
    spy_notify.assert_awaited_once()

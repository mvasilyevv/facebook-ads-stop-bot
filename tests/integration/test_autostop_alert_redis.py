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
    UNDELIVERED_ESCALATE_DEDUP_PREFIX,
    escalate_undelivered_autostop_pauses,
    maybe_alert_autostop_channel_down,
    record_autostop_success,
    register_autostop_failure_and_should_alert,
)
from core.meta_api.errors import RateLimitedError, TemporaryError


# ── Fake async engine: отдаёт заранее заданные строки на любой conn.execute().fetchall() ──
class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return _FakeResult(self._rows)


class _FakeEngine:
    def __init__(self, rows):
        self._rows = rows

    def connect(self):
        return _FakeConn(self._rows)


class _Row:
    """Минимальная строка стрянувшей задачи (доступ по атрибутам, как у asyncpg Row)."""

    def __init__(self, fb_ad_id, created_at, last_error="Failed to fetch"):
        self.id = 1
        self.fb_ad_id = fb_ad_id
        self.created_at = created_at
        self.attempt_count = 5
        self.last_error = last_error


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


# ─────────────── Per-ad эскалация недоставленной паузы ───────────────


# Застрявшая pause_ad → один per-ad алерт + дедуп; повтор в окне — молчим
@pytest.mark.asyncio
async def test_escalate_undelivered_sends_once(fake_redis_client, monkeypatch) -> None:
    from datetime import datetime, timedelta, timezone

    import core.meta_api.autostop_alert as mod

    created = datetime.now(timezone.utc) - timedelta(minutes=15)
    engine = _FakeEngine([_Row("120246662749510044", created)])

    # Не ходим в БД за ad_name/spend — отдаём фикс.
    async def fake_fetch(_engine, _fid):
        return ("GH_CR2_001", "55.00")

    monkeypatch.setattr(mod, "_fetch_ad_name_and_spend", fake_fetch)
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr("core.telegram.worker_notify.notify_owners", spy)

    # 1-й прогон — алерт + per-ad дедуп (throttle off для детерминизма)
    sent1 = await mod.escalate_undelivered_autostop_pauses(
        engine,
        fake_redis_client,
        stuck_after_seconds=600,
        dedup_ttl_seconds=3600,
        throttle_seconds=0,
    )
    assert sent1 == 1
    spy.assert_awaited_once()
    dedup = await fake_redis_client.get(UNDELIVERED_ESCALATE_DEDUP_PREFIX + "120246662749510044")
    assert dedup is not None

    # 2-й прогон в окне дедупа — тишина (не задваиваем «выключи вручную»)
    sent2 = await mod.escalate_undelivered_autostop_pauses(
        engine,
        fake_redis_client,
        stuck_after_seconds=600,
        dedup_ttl_seconds=3600,
        throttle_seconds=0,
    )
    assert sent2 == 0
    spy.assert_awaited_once()


# Нет застрявших задач → ноль алертов
@pytest.mark.asyncio
async def test_escalate_undelivered_no_stuck(fake_redis_client, monkeypatch) -> None:
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr("core.telegram.worker_notify.notify_owners", spy)
    sent = await escalate_undelivered_autostop_pauses(
        _FakeEngine([]), fake_redis_client, throttle_seconds=0
    )
    assert sent == 0
    spy.assert_not_awaited()


# Троттл: второй прогон подряд (лок держится) не сканирует и не шлёт
@pytest.mark.asyncio
async def test_escalate_undelivered_throttled(fake_redis_client, monkeypatch) -> None:
    from datetime import datetime, timedelta, timezone

    import core.meta_api.autostop_alert as mod

    created = datetime.now(timezone.utc) - timedelta(minutes=15)
    engine = _FakeEngine([_Row("999", created)])

    async def fake_fetch(_engine, _fid):
        return ("AD", "1.00")

    monkeypatch.setattr(mod, "_fetch_ad_name_and_spend", fake_fetch)
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr("core.telegram.worker_notify.notify_owners", spy)

    sent1 = await mod.escalate_undelivered_autostop_pauses(
        engine, fake_redis_client, stuck_after_seconds=600, throttle_seconds=60
    )
    assert sent1 == 1
    # Лок ещё держится → второй прогон молчит (даже если бы дедуп ad сняли)
    await fake_redis_client.delete(UNDELIVERED_ESCALATE_DEDUP_PREFIX + "999")
    sent2 = await mod.escalate_undelivered_autostop_pauses(
        engine, fake_redis_client, stuck_after_seconds=600, throttle_seconds=60
    )
    assert sent2 == 0
    spy.assert_awaited_once()

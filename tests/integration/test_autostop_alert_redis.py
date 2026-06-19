# -*- coding: utf-8 -*-
"""Интеграционные тесты autostop_alert: fakeredis-счётчик подряд-фейлов + дедуп + сброс.

Сценарий: money-канал auto-stop мёртв → после N подряд сетевых фейлов ОДИН CRITICAL в TG;
успех любого auto-stop сбрасывает счётчик и снимает дедуп (re-arm на следующий outage).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from core.meta_api.autostop_alert import (
    AUTOSTOP_ALERT_DEDUP_KEY,
    AUTOSTOP_FAIL_COUNTER_KEY,
    maybe_alert_autostop_channel_down,
    record_autostop_success,
    register_autostop_failure_and_should_alert,
)
from core.meta_api.errors import RateLimitedError, TemporaryError


@dataclass
class FakeTGClient:
    """Стаб TelegramBotClient: фиксирует send_message вызовы."""

    sent: list[dict] = field(default_factory=list)

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        message_thread_id: int | None = None,
        reply_markup: dict | None = None,
        parse_mode: str | None = "HTML",
    ) -> dict:
        self.sent.append({"chat_id": chat_id, "text": text, "thread_id": message_thread_id})
        return {"message_id": len(self.sent)}


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


# Оркестрация: channel-down на пороге → один CRITICAL в ops-тред с ad_id
@pytest.mark.asyncio
async def test_orchestrator_alerts_on_channel_down(fake_redis_client) -> None:
    tg = FakeTGClient()
    exc = TemporaryError("Failed to fetch", code=-2)
    sent_any = False
    for _ in range(3):
        sent = await maybe_alert_autostop_channel_down(
            fake_redis_client,
            exc=exc,
            fb_ad_id="120246662749510044",
            tg_client=tg,
            chat_id="100",
            thread_id=7,
            threshold=3,
            window_seconds=1800,
            dedup_ttl_seconds=1800,
        )
        sent_any = sent_any or sent
    assert sent_any is True
    assert len(tg.sent) == 1
    assert tg.sent[0]["thread_id"] == 7
    assert "120246662749510044" in tg.sent[0]["text"]


# Rate-limit (Meta-side, канал жив) НЕ инкрементит счётчик и НЕ алертит
@pytest.mark.asyncio
async def test_orchestrator_ignores_rate_limit(fake_redis_client) -> None:
    tg = FakeTGClient()
    exc = RateLimitedError("throttled", code=4)
    for _ in range(10):
        sent = await maybe_alert_autostop_channel_down(
            fake_redis_client,
            exc=exc,
            fb_ad_id="120246662749510044",
            tg_client=tg,
            chat_id="100",
            thread_id=7,
            threshold=3,
            window_seconds=1800,
            dedup_ttl_seconds=1800,
        )
        assert sent is False
    assert tg.sent == []
    # счётчик не появился — rate-limit не считается «каналом мёртв»
    assert await fake_redis_client.get(AUTOSTOP_FAIL_COUNTER_KEY) is None


# Нет TG-клиента → не падаем (алерт только в лог), решение всё равно True на пороге
@pytest.mark.asyncio
async def test_orchestrator_no_tg_client_does_not_crash(fake_redis_client) -> None:
    exc = TemporaryError("Failed to fetch", code=-2)
    last = False
    for _ in range(3):
        last = await maybe_alert_autostop_channel_down(
            fake_redis_client,
            exc=exc,
            fb_ad_id="120246662749510044",
            tg_client=None,
            chat_id=None,
            thread_id=None,
            threshold=3,
            window_seconds=1800,
            dedup_ttl_seconds=1800,
        )
    assert last is True

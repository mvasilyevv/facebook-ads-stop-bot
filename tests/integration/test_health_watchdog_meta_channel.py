# -*- coding: utf-8 -*-
"""Интеграционные тесты сетевого probe канала Marketing API в health_watchdog.

Инцидент 2026-06-19: token-only health не видел сетевого отказа канала auto-stop.
check_meta_api_channel — единственный прободер: зовёт check_health(full_probe=True),
пишет результат в Redis meta_api:channel:health (его читает health_details) и при
отказе шлёт CRITICAL-алерт с дедупом. Проверяем sync probe→Redis→alert на fakeredis.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from apps.health_watchdog.main import (
    META_CHANNEL_DEDUP_KEY,
    META_CHANNEL_HEALTH_KEY,
    check_meta_api_channel,
)


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
        parse_mode: str | None = "HTML",
    ) -> dict:
        self.sent.append({"chat_id": chat_id, "text": text, "thread_id": message_thread_id})
        return {"message_id": len(self.sent)}

    async def close(self) -> None:
        pass


@dataclass
class FakeMetaClient:
    """Фейк MetaApiClient: отдаёт заранее заданные ответы check_health по порядку."""

    responses: list[dict]
    calls: int = 0

    async def check_health(self, *, full_probe: bool = False) -> dict:
        assert full_probe is True, "watchdog обязан звать probe в full режиме"
        self.calls += 1
        idx = min(self.calls - 1, len(self.responses) - 1)
        return self.responses[idx]


_OK_PROBE = {
    "healthy": True,
    "current_url": "https://adsmanager.facebook.com/",
    "token_present": True,
    "token_length": 200,
    "detail": "ok",
    "probe_performed": True,
    "probe_ok": True,
    "probe_status_code": 200,
    "probe_duration_ms": 120,
    "probe_detail": "ok",
}

_DOWN_PROBE = {
    "healthy": False,
    "current_url": "https://adsmanager.facebook.com/",
    "token_present": True,
    "token_length": 200,
    "detail": "probe_network_down",
    "probe_performed": True,
    "probe_ok": False,
    "probe_status_code": 0,
    "probe_duration_ms": 0,
    "probe_detail": "probe_network_down",
}


# Канал мёртв (Failed to fetch): Redis-ключ healthy=False + ровно один CRITICAL-алерт
@pytest.mark.asyncio
async def test_probe_down_writes_redis_and_alerts(fake_redis_client) -> None:
    meta = FakeMetaClient([_DOWN_PROBE])
    tg = FakeTGClient()

    alerted = await check_meta_api_channel(
        meta,
        fake_redis_client,
        tg_client=tg,
        chat_id="100",
        thread_id=7,
    )

    assert alerted is True
    assert len(tg.sent) == 1
    assert "probe_network_down" in tg.sent[0]["text"]
    raw = await fake_redis_client.get(META_CHANNEL_HEALTH_KEY)
    payload = json.loads(raw)
    assert payload["healthy"] is False
    assert payload["probe_detail"] == "probe_network_down"
    assert "checked_at" in payload
    # дедуп выставлен
    assert await fake_redis_client.get(META_CHANNEL_DEDUP_KEY) == "1"


# Повторный отказ в окне дедупа → второй алерт не уходит
@pytest.mark.asyncio
async def test_probe_down_dedup_second_check_silent(fake_redis_client) -> None:
    meta = FakeMetaClient([_DOWN_PROBE, _DOWN_PROBE])
    tg = FakeTGClient()

    first = await check_meta_api_channel(
        meta, fake_redis_client, tg_client=tg, chat_id="100", thread_id=None
    )
    second = await check_meta_api_channel(
        meta, fake_redis_client, tg_client=tg, chat_id="100", thread_id=None
    )

    assert first is True
    assert second is False
    assert len(tg.sent) == 1


# Канал жив: Redis-ключ healthy=True, без алерта
@pytest.mark.asyncio
async def test_probe_ok_writes_redis_no_alert(fake_redis_client) -> None:
    meta = FakeMetaClient([_OK_PROBE])
    tg = FakeTGClient()

    alerted = await check_meta_api_channel(
        meta, fake_redis_client, tg_client=tg, chat_id="100", thread_id=None
    )

    assert alerted is False
    assert tg.sent == []
    payload = json.loads(await fake_redis_client.get(META_CHANNEL_HEALTH_KEY))
    assert payload["healthy"] is True
    assert payload["probe_ok"] is True


# Восстановление re-arm: down(alert) → ok(снимает дедуп) → down снова → новый alert
@pytest.mark.asyncio
async def test_probe_recovery_rearms_alert(fake_redis_client) -> None:
    meta = FakeMetaClient([_DOWN_PROBE, _OK_PROBE, _DOWN_PROBE])
    tg = FakeTGClient()

    a1 = await check_meta_api_channel(
        meta, fake_redis_client, tg_client=tg, chat_id="100", thread_id=None
    )
    a2 = await check_meta_api_channel(
        meta, fake_redis_client, tg_client=tg, chat_id="100", thread_id=None
    )
    a3 = await check_meta_api_channel(
        meta, fake_redis_client, tg_client=tg, chat_id="100", thread_id=None
    )

    assert a1 is True  # первый отказ → алерт
    assert a2 is False  # восстановление → молчим, снимаем дедуп
    assert a3 is True  # снова отказ → новый алерт (re-arm сработал)
    assert len(tg.sent) == 2


# check_health бросил исключение → канал считается мёртвым (Redis down + alert)
@pytest.mark.asyncio
async def test_probe_exception_treated_as_down(fake_redis_client) -> None:
    class BoomClient:
        async def check_health(self, *, full_probe: bool = False) -> dict:
            raise RuntimeError("gRPC boom")

    tg = FakeTGClient()
    alerted = await check_meta_api_channel(
        BoomClient(), fake_redis_client, tg_client=tg, chat_id="100", thread_id=None
    )

    assert alerted is True
    payload = json.loads(await fake_redis_client.get(META_CHANNEL_HEALTH_KEY))
    assert payload["healthy"] is False

# -*- coding: utf-8 -*-
"""Интеграционные тесты сетевого probe канала Marketing API в health_watchdog.

Инцидент 2026-06-19: token-only health не видел сетевого отказа канала auto-stop.
check_meta_api_channel — единственный прободер: зовёт check_health(full_probe=True),
пишет результат в Redis meta_api:channel:health (его читает health_details) и при
отказе шлёт CRITICAL-алерт с дедупом. Проверяем sync probe→Redis→alert на fakeredis.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.health_watchdog.main import (
    META_CHANNEL_DEDUP_KEY,
    META_CHANNEL_HEALTH_KEY,
    check_meta_api_channel,
)


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


def _make_engine():
    return MagicMock()


# Канал мёртв (Failed to fetch): Redis-ключ healthy=False + ровно один CRITICAL через notify_recipients
@pytest.mark.asyncio
async def test_probe_down_writes_redis_and_alerts(fake_redis_client) -> None:
    meta = FakeMetaClient([_DOWN_PROBE])
    engine = _make_engine()

    with patch("apps.health_watchdog.main.notify_recipients", AsyncMock(return_value=True)) as spy:
        alerted = await check_meta_api_channel(meta, fake_redis_client, engine=engine)

    assert alerted is True
    spy.assert_awaited_once()
    kwargs = spy.await_args.kwargs
    assert "probe_network_down" in kwargs["text"]
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
    engine = _make_engine()

    with patch("apps.health_watchdog.main.notify_recipients", AsyncMock(return_value=True)) as spy:
        first = await check_meta_api_channel(meta, fake_redis_client, engine=engine)
        second = await check_meta_api_channel(meta, fake_redis_client, engine=engine)

    assert first is True
    assert second is False
    assert spy.await_count == 1


# Канал жив: Redis-ключ healthy=True, без алерта
@pytest.mark.asyncio
async def test_probe_ok_writes_redis_no_alert(fake_redis_client) -> None:
    meta = FakeMetaClient([_OK_PROBE])
    engine = _make_engine()

    with patch("apps.health_watchdog.main.notify_recipients", AsyncMock(return_value=True)) as spy:
        alerted = await check_meta_api_channel(meta, fake_redis_client, engine=engine)

    assert alerted is False
    spy.assert_not_awaited()
    payload = json.loads(await fake_redis_client.get(META_CHANNEL_HEALTH_KEY))
    assert payload["healthy"] is True
    assert payload["probe_ok"] is True


# Восстановление re-arm: down(alert) → ok(снимает дедуп) → down снова → новый alert
@pytest.mark.asyncio
async def test_probe_recovery_rearms_alert(fake_redis_client) -> None:
    meta = FakeMetaClient([_DOWN_PROBE, _OK_PROBE, _DOWN_PROBE])
    engine = _make_engine()
    spy = AsyncMock(return_value=True)

    with patch("apps.health_watchdog.main.notify_recipients", spy):
        a1 = await check_meta_api_channel(meta, fake_redis_client, engine=engine)
        a2 = await check_meta_api_channel(meta, fake_redis_client, engine=engine)
        a3 = await check_meta_api_channel(meta, fake_redis_client, engine=engine)

    assert a1 is True  # первый отказ → алерт
    assert a2 is False  # восстановление → молчим, снимаем дедуп
    assert a3 is True  # снова отказ → новый алерт (re-arm сработал)
    assert spy.await_count == 2


# check_health бросил исключение → канал считается мёртвым (Redis down + alert)
@pytest.mark.asyncio
async def test_probe_exception_treated_as_down(fake_redis_client) -> None:
    class BoomClient:
        async def check_health(self, *, full_probe: bool = False) -> dict:
            raise RuntimeError("gRPC boom")

    engine = _make_engine()

    with patch("apps.health_watchdog.main.notify_recipients", AsyncMock(return_value=True)) as spy:
        alerted = await check_meta_api_channel(BoomClient(), fake_redis_client, engine=engine)

    assert alerted is True
    spy.assert_awaited_once()
    payload = json.loads(await fake_redis_client.get(META_CHANNEL_HEALTH_KEY))
    assert payload["healthy"] is False

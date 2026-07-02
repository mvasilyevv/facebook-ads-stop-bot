# -*- coding: utf-8 -*-
"""health_watchdog: живучесть циклов + наблюдаемость probe-прохода (инцидент 01.07).

Два урока инцидента:
1. main_loop = asyncio.gather без защиты — упавший/зависший цикл молча гасит воркер,
   и «сторож без сторожа» никем не перезапускается → _supervised рестартует цикл.
2. Успех отправки CRITICAL и пропуск по дедупу/выключенному сканированию НЕ логировались —
   тишина в логах неотличима от зависания (расследование 01.07 приняло успешную
   доставку за провал). Каждый проход probe обязан оставлять след в логе.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from unittest.mock import AsyncMock

import pytest

import apps.health_watchdog.main as hw


def _redis_mock(*, dedup_value=None):
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=dedup_value)
    redis.set = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    return redis


def _probe_client(probe: dict) -> AsyncMock:
    client = AsyncMock()
    client.check_health = AsyncMock(return_value=probe)
    return client


def _scanning(monkeypatch, enabled: bool) -> None:
    import core.observer.queries as oq

    monkeypatch.setattr(
        oq, "load_observer_config", AsyncMock(return_value={"is_scanning_enabled": enabled})
    )


# _supervised: цикл упал с исключением → лог + перезапуск; после stop — штатный выход
@pytest.mark.asyncio
async def test_supervised_restarts_crashed_loop(monkeypatch, caplog):
    monkeypatch.setattr(hw, "LOOP_RESTART_DELAY_SECONDS", 0.01)
    stop = asyncio.Event()
    calls = {"n": 0}

    async def flaky() -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        stop.set()

    with caplog.at_level(logging.ERROR, logger="health_watchdog"):
        await asyncio.wait_for(hw._supervised("flaky", flaky, stop), timeout=2)

    assert calls["n"] == 2
    assert any("flaky" in r.getMessage() for r in caplog.records)


# _supervised: цикл вышел сам при выставленном stop → перезапуска нет
@pytest.mark.asyncio
async def test_supervised_exits_on_stop(monkeypatch):
    monkeypatch.setattr(hw, "LOOP_RESTART_DELAY_SECONDS", 0.01)
    stop = asyncio.Event()
    calls = {"n": 0}

    async def clean() -> None:
        calls["n"] += 1
        stop.set()

    await asyncio.wait_for(hw._supervised("clean", clean, stop), timeout=2)
    assert calls["n"] == 1


# Контракт-пин: main_loop оборачивает циклы в _supervised (анти-регресс проводки)
def test_main_loop_uses_supervisor():
    src = inspect.getsource(hw.main_loop)
    assert "_supervised(" in src


# Probe: канал жив → INFO-след прохода (тишина ≠ зависание)
@pytest.mark.asyncio
async def test_probe_healthy_pass_leaves_log_trace(monkeypatch, caplog):
    _scanning(monkeypatch, True)
    client = _probe_client(
        {"healthy": True, "probe_performed": True, "probe_ok": True, "probe_detail": "ok"}
    )
    redis = _redis_mock()

    with caplog.at_level(logging.INFO, logger="health_watchdog"):
        alerted = await hw.check_meta_api_channel(client, redis, engine=object())

    assert alerted is False
    assert any("жив" in r.getMessage() for r in caplog.records)


# Probe: сканирование выключено → INFO о намеренном пропуске, алертов нет
@pytest.mark.asyncio
async def test_probe_scanning_off_logs_and_skips(monkeypatch, caplog):
    _scanning(monkeypatch, False)
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(hw, "notify_recipients", spy)
    client = _probe_client({"healthy": True})
    redis = _redis_mock()

    with caplog.at_level(logging.INFO, logger="health_watchdog"):
        alerted = await hw.check_meta_api_channel(client, redis, engine=object())

    assert alerted is False
    spy.assert_not_awaited()
    assert any("выключено" in r.getMessage() for r in caplog.records)


# Probe: канал мёртв → CRITICAL доставлен через notify_recipients + INFO «отправлен»
@pytest.mark.asyncio
async def test_probe_down_delivers_critical_with_trace(monkeypatch, caplog):
    _scanning(monkeypatch, True)
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(hw, "notify_recipients", spy)
    client = _probe_client(
        {
            "healthy": False,
            "probe_performed": True,
            "probe_ok": False,
            "probe_detail": "probe_network_down",
            "detail": "Failed to fetch",
        }
    )
    redis = _redis_mock(dedup_value=None)

    with caplog.at_level(logging.INFO, logger="health_watchdog"):
        alerted = await hw.check_meta_api_channel(client, redis, engine=object())

    assert alerted is True
    spy.assert_awaited_once()
    assert "CRITICAL" in spy.await_args.kwargs["text"]
    assert any("отправлен" in r.getMessage() for r in caplog.records)


# Probe: канал мёртв, дедуп стоит → отправки нет, но след «подавлен дедупом» есть
@pytest.mark.asyncio
async def test_probe_down_dedup_suppression_is_visible(monkeypatch, caplog):
    _scanning(monkeypatch, True)
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(hw, "notify_recipients", spy)
    client = _probe_client(
        {
            "healthy": False,
            "probe_performed": True,
            "probe_ok": False,
            "probe_detail": "probe_network_down",
            "detail": "Failed to fetch",
        }
    )
    redis = _redis_mock(dedup_value="1")  # алерт уже отправлен в этом окне

    with caplog.at_level(logging.INFO, logger="health_watchdog"):
        alerted = await hw.check_meta_api_channel(client, redis, engine=object())

    assert alerted is False
    spy.assert_not_awaited()
    assert any("подавлен" in r.getMessage() for r in caplog.records)

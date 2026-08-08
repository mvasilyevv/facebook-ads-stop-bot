# -*- coding: utf-8 -*-
"""health_watchdog: живучесть циклов + наблюдаемость probe-прохода (инцидент 01.07).

Два урока инцидента:
1. main_loop = asyncio.gather без защиты — упавший/зависший цикл молча гасит воркер,
   и «сторож без сторожа» никем не перезапускается → _supervised рестартует цикл.
2. Каждый проход probe обязан оставлять след в логе; notification intent фиксируется
   через PostgreSQL outbox.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from unittest.mock import AsyncMock

import pytest

import apps.health_watchdog.main as hw


@pytest.fixture(autouse=True)
def durable_browser_fence(monkeypatch):
    class FakeBrowserOperationFence:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def assert_held(self):
            return None

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(hw, "BrowserOperationFence", FakeBrowserOperationFence)
    monkeypatch.setattr(
        hw,
        "_load_canonical_vision_profile_id",
        AsyncMock(return_value="vision-profile-1"),
    )


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
    resolve = AsyncMock(return_value=False)
    monkeypatch.setattr(hw, "_resolve_critical_notification", resolve)
    client = _probe_client(
        {
            "healthy": True,
            "probe_performed": True,
            "probe_ok": True,
            "probe_detail": "ok",
            "browser_contract_version": hw.BROWSER_CONTRACT_VERSION,
            "vision_profile_id": "vision-profile-1",
        }
    )
    with caplog.at_level(logging.INFO, logger="health_watchdog"):
        alerted = await hw.check_meta_api_channel(client, engine=object())

    assert alerted is False
    assert any("жив" in r.getMessage() for r in caplog.records)


# Probe: сканирование выключено → INFO о намеренном пропуске, алертов нет
@pytest.mark.asyncio
async def test_probe_scanning_off_logs_and_skips(monkeypatch, caplog):
    _scanning(monkeypatch, False)
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(hw, "notify_recurring_incident", spy)
    client = _probe_client({"healthy": True})
    with caplog.at_level(logging.INFO, logger="health_watchdog"):
        alerted = await hw.check_meta_api_channel(client, engine=object())

    assert alerted is False
    spy.assert_not_awaited()
    assert any("выключено" in r.getMessage() for r in caplog.records)


# Probe: канал мёртв → CRITICAL принят durable outbox + INFO trace.
@pytest.mark.asyncio
async def test_probe_down_delivers_critical_with_trace(monkeypatch, caplog):
    _scanning(monkeypatch, True)
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(hw, "notify_recurring_incident", spy)
    client = _probe_client(
        {
            "healthy": False,
            "probe_performed": True,
            "probe_ok": False,
            "probe_detail": "probe_network_down",
            "detail": "Failed to fetch",
        }
    )
    with caplog.at_level(logging.INFO, logger="health_watchdog"):
        alerted = await hw.check_meta_api_channel(client, engine=object())

    assert alerted is True
    spy.assert_awaited_once()
    assert spy.await_args.kwargs["severity"] == "critical"
    assert "Marketing API" in spy.await_args.kwargs["title"]
    assert any("принят durable plane" in r.getMessage() for r in caplog.records)


# Probe failures are persisted without any Redis liveness dependency.
@pytest.mark.asyncio
async def test_probe_down_has_no_redis_dependency(monkeypatch):
    _scanning(monkeypatch, True)
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(hw, "notify_recurring_incident", spy)
    client = _probe_client(
        {
            "healthy": False,
            "probe_performed": True,
            "probe_ok": False,
            "probe_detail": "probe_network_down",
            "detail": "Failed to fetch",
        }
    )
    alerted = await hw.check_meta_api_channel(client, engine=object())

    assert alerted is True
    spy.assert_awaited_once()
    assert spy.await_args.kwargs["incident_key"] == hw.META_CHANNEL_INCIDENT_KEY

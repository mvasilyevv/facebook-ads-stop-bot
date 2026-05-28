# -*- coding: utf-8 -*-
"""Интеграционные тесты: worker'ы реагируют на Redis pubsub-сигналы.

Используем fakeredis (in-memory) — не требует живого Redis и реального toggle.
"""

from __future__ import annotations

import asyncio
import json

import pytest

# ====================== Helpers ======================

pytest.importorskip("fakeredis")


def _make_fake_redis():
    """Создаёт in-memory FakeRedis с decode_responses=True."""
    import fakeredis.aioredis  # type: ignore

    return fakeredis.aioredis.FakeRedis(decode_responses=True)


# ====================== Тесты observer worker ======================


# observer worker: trigger scan-now выставляет флаг force_scan_pending в shared state.
@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_observer_trigger_sets_force_scan_flag() -> None:
    from apps.observer_worker.main import (
        CHANNEL_TRIGGER,
        _ObserverState,
    )
    from core.control.pubsub_listener import RedisPubSubListener

    redis_client = _make_fake_redis()
    state = _ObserverState()

    listener = RedisPubSubListener(redis_client, [CHANNEL_TRIGGER])

    async def _on_trigger(_payload: dict) -> None:
        state.force_scan_pending = True

    listener.register(CHANNEL_TRIGGER, _on_trigger)
    task = asyncio.create_task(listener.run_forever())
    await asyncio.sleep(0.1)

    # Публикуем сигнал.
    await redis_client.publish(CHANNEL_TRIGGER, json.dumps({"reason": "manual"}))
    await asyncio.sleep(0.3)

    await listener.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        await redis_client.aclose()

    assert state.force_scan_pending is True


# observer worker: restart-сигнал выставляет should_stop → цикл завершается.
@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_observer_restart_sets_should_stop() -> None:
    from apps.observer_worker.main import (
        CHANNEL_RESTART,
        _ObserverState,
    )
    from core.control.pubsub_listener import RedisPubSubListener

    redis_client = _make_fake_redis()
    state = _ObserverState()
    shutdown_event = asyncio.Event()

    listener = RedisPubSubListener(redis_client, [CHANNEL_RESTART])

    async def _on_restart(_payload: dict) -> None:
        state.should_stop = True
        shutdown_event.set()

    listener.register(CHANNEL_RESTART, _on_restart)
    task = asyncio.create_task(listener.run_forever())
    await asyncio.sleep(0.1)

    await redis_client.publish(CHANNEL_RESTART, json.dumps({}))

    # Ждём пока shutdown_event не будет выставлен handler'ом.
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pytest.fail("shutdown_event не выставлен за 2 сек")

    await listener.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        await redis_client.aclose()

    assert state.should_stop is True
    assert shutdown_event.is_set()


# ====================== Тесты disable worker ======================


# disable worker: restart-сигнал выставляет stop_event → toggle-loop завершается.
@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_disable_worker_restart_stops_loop() -> None:
    from apps.disable_worker.main import CHANNEL_RESTART
    from core.control.pubsub_listener import RedisPubSubListener

    redis_client = _make_fake_redis()
    stop_event = asyncio.Event()

    listener = RedisPubSubListener(redis_client, [CHANNEL_RESTART])

    async def _on_restart(_payload: dict) -> None:
        stop_event.set()

    listener.register(CHANNEL_RESTART, _on_restart)
    task = asyncio.create_task(listener.run_forever())
    await asyncio.sleep(0.1)

    await redis_client.publish(CHANNEL_RESTART, json.dumps({"reason": "api_request"}))

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pytest.fail("stop_event не выставлен за 2 сек после publish в disable restart")

    await listener.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        await redis_client.aclose()

    assert stop_event.is_set()


# Исключение в handler не ломает listener-loop — следующий сигнал обрабатывается.
@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_handler_exception_does_not_crash_loop() -> None:
    from core.control.pubsub_listener import RedisPubSubListener

    redis_client = _make_fake_redis()
    channel = "test:crash:channel"
    good_calls: list[int] = []

    async def crashing_handler(_payload: dict) -> None:
        raise ValueError("намеренная ошибка в handler'е")

    async def counting_handler(payload: dict) -> None:
        good_calls.append(payload.get("n", 0))

    listener = RedisPubSubListener(redis_client, [channel])
    listener.register(channel, crashing_handler)
    listener.register(channel, counting_handler)

    task = asyncio.create_task(listener.run_forever())
    await asyncio.sleep(0.1)

    # Публикуем два сообщения — оба должны дойти до counting_handler.
    await redis_client.publish(channel, json.dumps({"n": 1}))
    await redis_client.publish(channel, json.dumps({"n": 2}))
    await asyncio.sleep(0.4)

    await listener.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        await redis_client.aclose()

    # counting_handler получил оба сообщения, несмотря на crashing_handler.
    assert sorted(good_calls) == [1, 2]

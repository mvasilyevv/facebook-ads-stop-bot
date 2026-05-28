# -*- coding: utf-8 -*-
"""Unit-тесты для core/control/pubsub_listener.RedisPubSubListener.

Используем fakeredis (in-memory) — не требует живого Redis.
FakeRedis создаётся внутри каждого теста (не через fixture), чтобы
избежать конфликтов event loop при asyncio_mode=auto.
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("fakeredis")

from core.control.pubsub_listener import RedisPubSubListener  # noqa: E402


def _make_fake_redis():
    """Создаёт in-memory FakeRedis с decode_responses=True."""
    import fakeredis.aioredis  # type: ignore

    return fakeredis.aioredis.FakeRedis(decode_responses=True)


# Один handler на один канал получает опубликованное сообщение.
@pytest.mark.asyncio
async def test_single_handler_dispatched() -> None:
    received: list[dict] = []
    fake_redis = _make_fake_redis()

    async def handler(payload: dict) -> None:
        received.append(payload)

    listener = RedisPubSubListener(fake_redis, ["test:channel"])
    listener.register("test:channel", handler)

    task = asyncio.create_task(listener.run_forever())
    await asyncio.sleep(0.1)

    await fake_redis.publish("test:channel", json.dumps({"action": "run"}))
    await asyncio.sleep(0.3)

    await listener.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        await fake_redis.aclose()

    assert len(received) == 1
    assert received[0]["action"] == "run"


# Несколько handlers на один канал — все вызываются.
@pytest.mark.asyncio
async def test_multiple_handlers_all_called() -> None:
    calls: list[str] = []
    fake_redis = _make_fake_redis()

    async def h1(payload: dict) -> None:
        calls.append("h1")

    async def h2(payload: dict) -> None:
        calls.append("h2")

    listener = RedisPubSubListener(fake_redis, ["multi:channel"])
    listener.register("multi:channel", h1)
    listener.register("multi:channel", h2)

    task = asyncio.create_task(listener.run_forever())
    await asyncio.sleep(0.1)

    await fake_redis.publish("multi:channel", json.dumps({"x": 1}))
    await asyncio.sleep(0.3)

    await listener.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        await fake_redis.aclose()

    assert "h1" in calls
    assert "h2" in calls


# Исключение в handler не роняет loop — следующее сообщение всё равно доходит.
@pytest.mark.asyncio
async def test_handler_exception_does_not_break_loop() -> None:
    good_calls: list[dict] = []
    fake_redis = _make_fake_redis()

    async def bad_handler(payload: dict) -> None:
        raise RuntimeError("intentional error")

    async def good_handler(payload: dict) -> None:
        good_calls.append(payload)

    listener = RedisPubSubListener(fake_redis, ["err:channel"])
    listener.register("err:channel", bad_handler)
    listener.register("err:channel", good_handler)

    task = asyncio.create_task(listener.run_forever())
    await asyncio.sleep(0.1)

    # Два сообщения — оба должны дойти до good_handler несмотря на bad_handler.
    await fake_redis.publish("err:channel", json.dumps({"n": 1}))
    await fake_redis.publish("err:channel", json.dumps({"n": 2}))
    await asyncio.sleep(0.4)

    await listener.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        await fake_redis.aclose()

    # good_handler получил оба сообщения.
    assert len(good_calls) == 2


# Несколько каналов — handler срабатывает только на свой канал.
@pytest.mark.asyncio
async def test_multiple_channels() -> None:
    ch_a_calls: list[dict] = []
    ch_b_calls: list[dict] = []
    fake_redis = _make_fake_redis()

    async def handler_a(payload: dict) -> None:
        ch_a_calls.append(payload)

    async def handler_b(payload: dict) -> None:
        ch_b_calls.append(payload)

    listener = RedisPubSubListener(fake_redis, ["chan:a", "chan:b"])
    listener.register("chan:a", handler_a)
    listener.register("chan:b", handler_b)

    task = asyncio.create_task(listener.run_forever())
    await asyncio.sleep(0.1)

    await fake_redis.publish("chan:a", json.dumps({"src": "a"}))
    await fake_redis.publish("chan:b", json.dumps({"src": "b"}))
    await asyncio.sleep(0.4)

    await listener.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        await fake_redis.aclose()

    assert len(ch_a_calls) == 1 and ch_a_calls[0]["src"] == "a"
    assert len(ch_b_calls) == 1 and ch_b_calls[0]["src"] == "b"


# Graceful stop — listener завершается после stop() без потери уже принятых сообщений.
@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_graceful_stop() -> None:
    received: list[dict] = []
    fake_redis = _make_fake_redis()

    async def handler(payload: dict) -> None:
        received.append(payload)

    listener = RedisPubSubListener(fake_redis, ["stop:channel"])
    listener.register("stop:channel", handler)

    task = asyncio.create_task(listener.run_forever())
    await asyncio.sleep(0.1)

    await fake_redis.publish("stop:channel", json.dumps({"seq": 1}))
    await asyncio.sleep(0.2)

    # Вызываем stop — loop должен завершиться.
    await listener.stop()
    try:
        await asyncio.wait_for(task, timeout=2.0)
    except asyncio.TimeoutError:
        task.cancel()
        await fake_redis.aclose()
        pytest.fail("listener не завершился за 2 сек после stop()")
    except asyncio.CancelledError:
        pass
    finally:
        await fake_redis.aclose()

    # Сообщение которое пришло ДО stop() — должно быть получено.
    assert len(received) >= 1
    assert received[0]["seq"] == 1

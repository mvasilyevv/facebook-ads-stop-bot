# -*- coding: utf-8 -*-
"""Базовые тесты fakeredis для disposable cache/rate-limit и wakeup channels.

Durable control, notification and process-liveness state is deliberately absent.
"""

from __future__ import annotations

import json

import pytest


# Сценарий: TTL контракт.
# fakeredis не запускает фоновую expiration thread'у — ключи не «сами протухают»
# с течением реального времени. Поэтому проверяем контракт через ttl():
# что TTL установлен и в правильных границах. Реальный Redis сам почистит.
@pytest.mark.asyncio
async def test_expiration_contract(fake_redis_client) -> None:
    await fake_redis_client.set("temp:key", "data", ex=60)
    ttl = await fake_redis_client.ttl("temp:key")
    assert 50 <= ttl <= 60, f"TTL вне ожидаемых границ: {ttl}"

    # Симуляция прошедшего времени через персистентный delete (как Redis делает в expire-thread'е)
    await fake_redis_client.delete("temp:key")
    assert await fake_redis_client.get("temp:key") is None

    # Без TTL → ttl() возвращает -1 (per Redis contract)
    await fake_redis_client.set("permanent", "x")
    ttl_no_expire = await fake_redis_client.ttl("permanent")
    assert ttl_no_expire == -1


# Сценарий: pub/sub используется только как accelerator durable tracker inbox.
@pytest.mark.asyncio
async def test_pubsub_publish_subscribe(fake_redis_client) -> None:
    pubsub = fake_redis_client.pubsub()
    await pubsub.subscribe("fb_agent:tracker:wakeup")

    # Drain subscribe-message
    await pubsub.get_message(timeout=0.5)

    # Publisher шлёт событие
    n_receivers = await fake_redis_client.publish(
        "fb_agent:tracker:wakeup",
        json.dumps({"event_id": 42}),
    )
    assert n_receivers == 1

    msg = await pubsub.get_message(timeout=1.0)
    assert msg is not None
    assert msg["type"] == "message"
    assert msg["channel"] == "fb_agent:tracker:wakeup"
    payload = json.loads(msg["data"])
    assert payload["event_id"] == 42

    await pubsub.unsubscribe("fb_agent:tracker:wakeup")
    await pubsub.aclose()


# Сценарий: DELETE для cleanup expired ai_cache
@pytest.mark.asyncio
async def test_pattern_delete(fake_redis_client) -> None:
    await fake_redis_client.set("ai:cache:overview:xxx:abc", "data1")
    await fake_redis_client.set("ai:cache:overview:xxx:def", "data2")
    await fake_redis_client.set("ai:cache:other:yyy:ghi", "data3")

    # Сканируем по паттерну (как сделает cleanup для конкретного scope)
    keys = []
    async for key in fake_redis_client.scan_iter(match="ai:cache:overview:*"):
        keys.append(key)
    assert len(keys) == 2

    if keys:
        await fake_redis_client.delete(*keys)

    remaining = await fake_redis_client.get("ai:cache:other:yyy:ghi")
    assert remaining == "data3"
    gone = await fake_redis_client.get("ai:cache:overview:xxx:abc")
    assert gone is None

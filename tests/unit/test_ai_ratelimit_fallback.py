# -*- coding: utf-8 -*-
"""Unit-тесты HIGH #13 — in-memory fallback при сбое Redis.

Покрываем сценарии:
- Redis рейзит → срабатывает in-memory cap (5 запросов / 60с).
- Разные client_key не делят bucket.
- 5 в пределах cap — проходят, 6-й — RateLimitExceeded.
"""

from __future__ import annotations

import pytest

from core.ai_assistant.tools._ratelimit import (
    RateLimitExceeded,
    _reset_memory_fallback_for_tests,
    check_and_increment,
)


class _BrokenRedis:
    """Имитация сбоя Redis — pipeline (incr/expire атомарно, LOW аудита 02.07) рейзит."""

    def pipeline(self, transaction: bool = True):
        raise RuntimeError("redis down")


@pytest.fixture(autouse=True)
def _reset_fallback():
    """Перед каждым тестом сбрасываем in-memory bucket."""
    _reset_memory_fallback_for_tests()
    yield
    _reset_memory_fallback_for_tests()


# 5 запросов подряд для одного client_key под cap проходят при оффлайн Redis.
@pytest.mark.asyncio
async def test_memory_fallback_allows_under_cap() -> None:
    redis = _BrokenRedis()
    for _ in range(5):
        result = await check_and_increment(redis, client_key="user-1")
        assert isinstance(result, int)


# 6-й запрос подряд после 5 — превышение cap, RateLimitExceeded.
@pytest.mark.asyncio
async def test_memory_fallback_blocks_over_cap() -> None:
    redis = _BrokenRedis()
    for _ in range(5):
        await check_and_increment(redis, client_key="user-2")
    with pytest.raises(RateLimitExceeded):
        await check_and_increment(redis, client_key="user-2")


# Разные client_key имеют отдельные bucket'ы — не делят cap между собой.
@pytest.mark.asyncio
async def test_memory_fallback_per_client_key_isolation() -> None:
    redis = _BrokenRedis()
    for _ in range(5):
        await check_and_increment(redis, client_key="alice")
    # Bob ещё не использовал — должен пройти, несмотря на исчерпание у alice.
    result = await check_and_increment(redis, client_key="bob")
    assert result == 1


# redis_client=None — fail-open (legacy режим без Redis вообще).
@pytest.mark.asyncio
async def test_no_redis_client_is_failopen() -> None:
    result = await check_and_increment(None, client_key="anyone")
    assert result == 0

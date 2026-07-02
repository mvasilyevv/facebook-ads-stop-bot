# -*- coding: utf-8 -*-
"""Unit-тесты Redis-backed rate-limit для /ai/analyze.

Проверяем:
1. Redis sliding-window: 21-й запрос → RateLimitExceeded.
2. X-Forwarded-For парсинг: используется первый IP из списка.
3. Fallback при сбое Redis: secondary in-memory cap (не fail-open).
4. Интеграция: роутер ai_analyze возвращает 429 при превышении Redis-лимита.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from apps.api.routers.v1.ai_analyze import _extract_client_key
from core.ai_assistant.tools._ratelimit import (
    RateLimitExceeded,
    _reset_memory_fallback_for_tests,
    check_and_increment,
)

# ─── _extract_client_key ─────────────────────────────────────────────────────


# Без X-Forwarded-For → берётся request.client.host
def test_extract_client_key_uses_client_host() -> None:
    """Без заголовка X-Forwarded-For используется request.client.host."""
    req = MagicMock()
    req.headers = {}
    req.client = MagicMock()
    req.client.host = "10.0.0.1"
    assert _extract_client_key(req) == "10.0.0.1"


# X-Forwarded-For: один IP → берётся тот IP (ТОЛЬКО за доверенным прокси, H7a)
def test_extract_client_key_xff_single_ip() -> None:
    """X-Forwarded-For с одним IP → используется этот IP при trust_proxy=True."""
    req = MagicMock()
    req.headers = {"X-Forwarded-For": "203.0.113.42"}
    req.client = MagicMock()
    req.client.host = "172.16.0.1"  # IP прокси — должен быть проигнорирован
    assert _extract_client_key(req, trust_proxy=True) == "203.0.113.42"


# X-Forwarded-For: цепочка proxy — берётся самый левый (реальный клиент, за прокси)
def test_extract_client_key_xff_chain_takes_first() -> None:
    """X-Forwarded-For с цепочкой → первый IP при trust_proxy=True."""
    req = MagicMock()
    req.headers = {"X-Forwarded-For": "203.0.113.42, 10.0.0.1, 192.168.1.1"}
    req.client = MagicMock()
    req.client.host = "172.16.0.1"
    assert _extract_client_key(req, trust_proxy=True) == "203.0.113.42"


# X-Forwarded-For пустая строка → fallback на client.host
def test_extract_client_key_xff_empty_string_fallback() -> None:
    """X-Forwarded-For пустой → fallback на client.host."""
    req = MagicMock()
    req.headers = {"X-Forwarded-For": ""}
    req.client = MagicMock()
    req.client.host = "10.0.0.5"
    assert _extract_client_key(req) == "10.0.0.5"


# request.client = None → 'unknown' вместо падения
def test_extract_client_key_no_client() -> None:
    """Нет request.client и нет X-Forwarded-For → 'unknown'."""
    req = MagicMock()
    req.headers = {}
    req.client = None
    assert _extract_client_key(req) == "unknown"


# ─── Redis sliding-window ──────────────────────────────────────────────────────


class _FakePipeline:
    """Минимальная эмуляция redis.asyncio Pipeline для MULTI/EXEC (LOW, аудит 02.07).

    check_and_increment шлёт INCR+EXPIRE одной атомарной транзакцией — закрывает окно
    гонки "краш между раздельными INCR и EXPIRE оставляет ключ без TTL навсегда".
    """

    def __init__(self, incr_fn):
        self._incr_fn = incr_fn
        self._incr_result: int | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def incr(self, key: str):
        self._pending_key = key
        return self

    def expire(self, key: str, ttl: int, nx: bool = False):
        return self

    async def execute(self):
        self._incr_result = await self._incr_fn(self._pending_key)
        return [self._incr_result, True]


# 20 запросов — проходят, 21-й — RateLimitExceeded
@pytest.mark.asyncio
async def test_redis_ratelimit_21st_request_raises() -> None:
    """21-й запрос к Redis rate-limit → RateLimitExceeded."""
    # Симулируем Redis: INCR возвращает нарастающее значение
    counter = {"value": 0}

    async def fake_incr(key: str) -> int:
        counter["value"] += 1
        return counter["value"]

    redis = MagicMock()
    redis.pipeline = MagicMock(side_effect=lambda transaction=True: _FakePipeline(fake_incr))

    # 20 запросов должны проходить
    for i in range(20):
        count = await check_and_increment(
            redis, client_key="test_ip", max_per_hour=20, namespace="analyze"
        )
        assert count == i + 1

    # 21-й — должен кинуть RateLimitExceeded
    with pytest.raises(RateLimitExceeded):
        await check_and_increment(redis, client_key="test_ip", max_per_hour=20, namespace="analyze")


# ─── Fallback при сбое Redis ──────────────────────────────────────────────────


# Redis кидает исключение → secondary in-memory cap (не fail-open)
@pytest.mark.asyncio
async def test_redis_ratelimit_fallback_on_redis_error() -> None:
    """Сбой Redis → in-memory fallback cap активируется, не fail-open."""
    _reset_memory_fallback_for_tests()

    redis = MagicMock()
    redis.pipeline = MagicMock(side_effect=ConnectionError("Redis недоступен"))

    # Проходим secondary cap (5 запросов / 60с)
    for _ in range(5):
        count = await check_and_increment(
            redis, client_key="fallback_ip", max_per_hour=20, namespace="analyze"
        )
        assert count >= 1

    # 6-й — должен кинуть RateLimitExceeded (secondary cap исчерпан)
    with pytest.raises(RateLimitExceeded):
        await check_and_increment(
            redis, client_key="fallback_ip", max_per_hour=20, namespace="analyze"
        )


# Примечание: интеграционный тест роутера /ai/analyze → 429 живёт в
# tests/integration/test_api_ai_analyze.py::test_ai_analyze_rate_limit_exceeded
# (там доступна fixture fake_redis_client). Здесь — только unit-логика.

# -*- coding: utf-8 -*-
"""Per-client_key rate-limit для AI tools поверх Redis.

Ключ Redis: ai:ratelimit:{namespace}:{client_key}
- INCR счётчика
- EXPIRE 3600s ставится только при первом увеличении (NX-семантика через INCR == 1)

Если Redis недоступен — переключаемся на in-memory secondary cap (sliding window).
In-memory лимит жёстче (5 запросов / 60 сек на process), чем Redis-лимит, —
это защита от лавины при сбое Redis. Полный fail-open недопустим: иначе при
оффлайн Redis злоумышленник может бомбардировать Meta API через AI-tools.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any

from core.safe_diagnostics import safe_exception_diagnostic

logger = logging.getLogger(__name__)

_DEFAULT_TTL_SECONDS = 3600
_DEFAULT_MAX_PER_HOUR = 30

# In-memory secondary cap — активируется только когда Redis рейзит.
# Минимальный, защищает от лавины при оффлайн Redis (DoS на Meta API).
_MEMORY_FALLBACK_MAX = 5
_MEMORY_FALLBACK_WINDOW = 60.0  # секунд

_memory_fallback_lock = asyncio.Lock()
_memory_fallback_buckets: dict[str, list[float]] = defaultdict(list)


class RateLimitExceeded(Exception):
    """Превышен лимит вызовов tool для client_key."""


def _reset_memory_fallback_for_tests() -> None:
    """Тестовый helper — очищает in-memory bucket'ы. Не использовать в проде."""
    _memory_fallback_buckets.clear()


async def _check_memory_fallback(client_key: str) -> int:
    """Sliding-window cap в памяти. Возвращает текущий размер bucket'а.

    Бросает RateLimitExceeded если bucket переполнен.
    """
    async with _memory_fallback_lock:
        now = time.monotonic()
        bucket = _memory_fallback_buckets[client_key]
        bucket[:] = [t for t in bucket if now - t < _MEMORY_FALLBACK_WINDOW]
        if len(bucket) >= _MEMORY_FALLBACK_MAX:
            logger.warning(
                "AI rate-limit: memory-fallback hit для %s (%d/%ds)",
                client_key,
                len(bucket),
                int(_MEMORY_FALLBACK_WINDOW),
            )
            raise RateLimitExceeded(
                f"Redis недоступен и in-memory лимит исчерпан "
                f"({_MEMORY_FALLBACK_MAX} запросов / {int(_MEMORY_FALLBACK_WINDOW)}с) "
                f"для client_key={client_key!r}"
            )
        bucket.append(now)
        return len(bucket)


async def check_and_increment(
    redis_client: Any,
    *,
    client_key: str,
    max_per_hour: int = _DEFAULT_MAX_PER_HOUR,
    namespace: str = "tools",
) -> int:
    """Проверить rate-limit и инкрементнуть счётчик.

    Возвращает текущее значение счётчика (после инкремента).
    Бросает RateLimitExceeded если лимит превышен.

    Fail behaviour:
    - redis_client=None → пропускаем БЕЗ инкремента (тестовый/MCP-режим).
    - Redis рейзит → in-memory fallback (жёсткий cap, защита от лавины).
    """
    if redis_client is None:
        logger.debug("rate-limit fail-open: redis_client=None (client_key=%s)", client_key)
        return 0

    key = f"ai:ratelimit:{namespace}:{client_key}"
    try:
        # LOW (аудит 02.07): INCR и EXPIRE двумя раздельными вызовами имели окно гонки —
        # краш/дисконнект между ними оставлял ключ БЕЗ TTL навсегда (утечка + перманентный
        # rate-limit для client_key). MULTI/EXEC через pipeline(transaction=True) шлёт обе
        # команды одной атомарной транзакцией — либо обе применились, либо ни одна.
        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, _DEFAULT_TTL_SECONDS, nx=True)
            incr_result, _expire_result = await pipe.execute()
        current = incr_result
    except Exception as exc:
        logger.warning(
            "rate-limit redis недоступен (%s), переключаюсь на in-memory cap",
            safe_exception_diagnostic(exc),
        )
        return await _check_memory_fallback(client_key)

    if int(current) > max_per_hour:
        raise RateLimitExceeded(
            f"Превышен лимит {max_per_hour} запросов/час для client_key={client_key!r}"
        )
    return int(current)

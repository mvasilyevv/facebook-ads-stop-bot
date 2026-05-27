# -*- coding: utf-8 -*-
"""Per-client_key rate-limit для AI tools поверх Redis.

Ключ Redis: ai:ratelimit:{client_key}
- INCR счётчика
- EXPIRE 3600s ставится только при первом увеличении (NX-семантика через if pttl<0)

Если redis недоступен — fail-open: разрешаем запрос, но логируем.
Логика hard-fail вынесена выше (rate-limit per ChatSession).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_TTL_SECONDS = 3600
_DEFAULT_MAX_PER_HOUR = 30


class RateLimitExceeded(Exception):
    """Превышен лимит вызовов tool для client_key."""


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

    Fail-open: если redis_client=None или redis рейзит — считаем что лимит не достигнут.
    """
    if redis_client is None:
        logger.debug("rate-limit fail-open: redis_client=None (client_key=%s)", client_key)
        return 0

    key = f"ai:ratelimit:{namespace}:{client_key}"
    try:
        # INCR атомарно создаёт ключ если нет, возвращает новое значение.
        current = await redis_client.incr(key)
        if current == 1:
            # Только что создали ключ — ставим TTL.
            await redis_client.expire(key, _DEFAULT_TTL_SECONDS)
    except Exception as exc:
        logger.warning("rate-limit redis недоступен, fail-open: %s", exc)
        return 0

    if int(current) > max_per_hour:
        raise RateLimitExceeded(
            f"Превышен лимит {max_per_hour} запросов/час для client_key={client_key!r}"
        )
    return int(current)

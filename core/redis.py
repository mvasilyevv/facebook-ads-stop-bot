from __future__ import annotations

from functools import lru_cache

import redis.asyncio as redis

from core.config import get_settings


@lru_cache(maxsize=1)
def get_redis_client() -> redis.Redis:
    """Возвращает единственный экземпляр async Redis-клиента."""
    settings = get_settings()
    return redis.from_url(settings.redis_url, decode_responses=True)

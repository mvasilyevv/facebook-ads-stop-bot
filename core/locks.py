from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as redis

_RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class ScanLockAcquisitionError(Exception):
    """Не удалось получить блокировку для сканирования профиля."""


@asynccontextmanager
async def acquire_scan_lock(
    redis_client: redis.Redis,
    profile_id: str,
    ttl_seconds: int = 300,
) -> AsyncIterator[None]:
    """Захватывает распределённую блокировку на время сканирования профиля."""

    lock_key = f"scan_lock:profile:{profile_id}"
    lock_value = str(uuid.uuid4())
    logger = logging.getLogger(__name__)

    acquired = await redis_client.set(lock_key, lock_value, nx=True, ex=ttl_seconds)
    if not acquired:
        raise ScanLockAcquisitionError(f"Профиль {profile_id} уже сканируется другим воркером")

    logger.info("Блокировка получена для профиля %s (TTL=%s сек)", profile_id, ttl_seconds)
    try:
        yield
    finally:
        await redis_client.eval(_RELEASE_LOCK_SCRIPT, 1, lock_key, lock_value)
        logger.info("Блокировка освобождена для профиля %s", profile_id)

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as redis


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
    logger = logging.getLogger(__name__)

    acquired = await redis_client.set(lock_key, "locked", nx=True, ex=ttl_seconds)
    if not acquired:
        raise ScanLockAcquisitionError(f"Профиль {profile_id} уже сканируется другим воркером")

    logger.info("Блокировка получена для профиля %s (TTL=%s сек)", profile_id, ttl_seconds)
    try:
        yield
    finally:
        await redis_client.delete(lock_key)
        logger.info("Блокировка освобождена для профиля %s", profile_id)

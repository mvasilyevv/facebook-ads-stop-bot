from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import redis.asyncio as redis

_RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

_RENEW_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
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
    heartbeat_task = asyncio.create_task(
        _renew_scan_lock(
            redis_client=redis_client,
            lock_key=lock_key,
            lock_value=lock_value,
            ttl_seconds=ttl_seconds,
        )
    )
    try:
        yield
    finally:
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task
        await redis_client.eval(_RELEASE_LOCK_SCRIPT, 1, lock_key, lock_value)
        logger.info("Блокировка освобождена для профиля %s", profile_id)


async def _renew_scan_lock(
    *,
    redis_client: redis.Redis,
    lock_key: str,
    lock_value: str,
    ttl_seconds: int,
) -> None:
    """Периодически продлевает блокировку, пока scan находится в работе."""

    logger = logging.getLogger(__name__)
    heartbeat_interval = max(1, min(60, ttl_seconds // 3))
    while True:
        try:
            await asyncio.sleep(heartbeat_interval)
            renewed = await redis_client.eval(
                _RENEW_LOCK_SCRIPT,
                1,
                lock_key,
                lock_value,
                ttl_seconds,
            )
            if renewed != 1:
                logger.warning(
                    "Не удалось продлить блокировку %s: она уже недоступна или была перехвачена",
                    lock_key,
                )
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Не удалось продлить блокировку %s: %s",
                lock_key,
                exc,
            )

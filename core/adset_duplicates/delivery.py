# -*- coding: utf-8 -*-
"""Redis lease для единственного отправителя Telegram-preview одного DRAFT."""

from __future__ import annotations

import secrets
from typing import Any

from redis.exceptions import WatchError


def _matches_owner(raw: Any, owner_token: str) -> bool:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    return raw == owner_token


async def acquire_delivery_lock(redis: Any, *, key: str, ttl_seconds: int) -> str | None:
    """SET NX lease с уникальным owner-token; None означает занятой lock."""
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds должен быть положительным")
    owner_token = secrets.token_urlsafe(24)
    acquired = await redis.set(key, owner_token, nx=True, ex=ttl_seconds)
    return owner_token if acquired else None


async def delivery_lock_owned(redis: Any, *, key: str, owner_token: str) -> bool:
    """Проверить ownership без изменения lease."""
    return _matches_owner(await redis.get(key), owner_token)


async def renew_delivery_lock(
    redis: Any,
    *,
    key: str,
    owner_token: str,
    ttl_seconds: int,
) -> bool:
    """Атомарно продлить lease, только если caller всё ещё владеет lock."""
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds должен быть положительным")
    for _ in range(4):
        try:
            async with redis.pipeline(transaction=True) as pipe:
                await pipe.watch(key)
                if not _matches_owner(await pipe.get(key), owner_token):
                    await pipe.unwatch()
                    return False
                pipe.multi()
                pipe.expire(key, ttl_seconds)
                result = await pipe.execute()
                return bool(result and result[0])
        except WatchError:
            continue
    return False


async def release_delivery_lock(redis: Any, *, key: str, owner_token: str) -> bool:
    """Compare-and-delete: никогда не удаляет lease следующего владельца."""
    for _ in range(4):
        try:
            async with redis.pipeline(transaction=True) as pipe:
                await pipe.watch(key)
                if not _matches_owner(await pipe.get(key), owner_token):
                    await pipe.unwatch()
                    return False
                pipe.multi()
                pipe.delete(key)
                result = await pipe.execute()
                return bool(result and result[0])
        except WatchError:
            continue
    return False


__all__ = [
    "acquire_delivery_lock",
    "delivery_lock_owned",
    "release_delivery_lock",
    "renew_delivery_lock",
]

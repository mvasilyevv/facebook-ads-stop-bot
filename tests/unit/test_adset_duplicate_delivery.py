# -*- coding: utf-8 -*-
"""Unit: безопасный Redis lease доставки DRAFT-подтверждения."""

from __future__ import annotations

import fakeredis.aioredis as fakeredis_aio  # type: ignore[import-not-found]
import pytest

from core.adset_duplicates.delivery import (
    acquire_delivery_lock,
    delivery_lock_owned,
    release_delivery_lock,
    renew_delivery_lock,
)


@pytest.mark.asyncio
async def test_delivery_lease_is_owned_renewed_and_released_by_unique_token() -> None:
    redis = fakeredis_aio.FakeRedis()
    key = "adset_duplicate:delivery:123"

    owner = await acquire_delivery_lock(redis, key=key, ttl_seconds=30)

    assert owner is not None
    assert await acquire_delivery_lock(redis, key=key, ttl_seconds=30) is None
    assert await delivery_lock_owned(redis, key=key, owner_token=owner)
    assert not await release_delivery_lock(redis, key=key, owner_token="another-request")
    assert await renew_delivery_lock(redis, key=key, owner_token=owner, ttl_seconds=60)
    assert await redis.ttl(key) > 30
    assert await release_delivery_lock(redis, key=key, owner_token=owner)
    assert not await delivery_lock_owned(redis, key=key, owner_token=owner)

    await redis.aclose()


@pytest.mark.asyncio
async def test_expired_owner_cannot_renew_or_delete_next_owners_lease() -> None:
    redis = fakeredis_aio.FakeRedis()
    key = "adset_duplicate:delivery:456"
    old_owner = await acquire_delivery_lock(redis, key=key, ttl_seconds=30)
    assert old_owner is not None

    await redis.set(key, "next-owner", ex=30)

    assert not await renew_delivery_lock(
        redis,
        key=key,
        owner_token=old_owner,
        ttl_seconds=30,
    )
    assert not await release_delivery_lock(redis, key=key, owner_token=old_owner)
    assert await redis.get(key) == b"next-owner"

    await redis.aclose()

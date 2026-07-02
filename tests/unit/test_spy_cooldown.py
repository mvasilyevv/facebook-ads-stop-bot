# -*- coding: utf-8 -*-
"""Rate-limit /spy (MID-9): per-user Redis-cooldown + глобальный Semaphore(1).

Redis мокается через _get_redis_client — unit-тесты НЕ ходят в живой Redis
(живой SET NX ставил реальный ключ на 120с и флакал соседние тесты).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.telegram.handlers import spy as spy_mod


# SET NX прошёл (ключа не было) → можно запускать, cooldown поставлен
@pytest.mark.asyncio
async def test_cooldown_free_allows():
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    with patch.object(spy_mod, "_get_redis_client", new=AsyncMock(return_value=redis)):
        assert await spy_mod._check_and_set_cooldown(555) is True
    args, kwargs = redis.set.call_args
    assert args[0] == f"{spy_mod._SPY_COOLDOWN_KEY_PREFIX}555"
    assert kwargs["nx"] is True
    assert kwargs["ex"] == spy_mod.SPY_COOLDOWN_SECONDS


# Ключ уже стоит (SET NX вернул falsy) → отказ, повторный запуск заблокирован
@pytest.mark.asyncio
async def test_cooldown_active_blocks():
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=None)
    with patch.object(spy_mod, "_get_redis_client", new=AsyncMock(return_value=redis)):
        assert await spy_mod._check_and_set_cooldown(555) is False


# Redis-клиент не создался → fail-open (инфраструктурный сбой не блокирует команду)
@pytest.mark.asyncio
async def test_cooldown_no_redis_fail_open():
    with patch.object(spy_mod, "_get_redis_client", new=AsyncMock(return_value=None)):
        assert await spy_mod._check_and_set_cooldown(555) is True


# Ошибка SET (Redis умер посреди запроса) → fail-open, не роняем handler
@pytest.mark.asyncio
async def test_cooldown_redis_error_fail_open():
    redis = AsyncMock()
    redis.set = AsyncMock(side_effect=ConnectionError("redis down"))
    with patch.object(spy_mod, "_get_redis_client", new=AsyncMock(return_value=redis)):
        assert await spy_mod._check_and_set_cooldown(555) is True

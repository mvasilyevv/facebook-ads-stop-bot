from __future__ import annotations

import pytest

from core.locks import ScanLockAcquisitionError, acquire_scan_lock


class FakeRedis:
    """Минимальная заглушка async Redis для тестирования блокировок."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True

    async def delete(self, key: str) -> int:
        return 1 if self._store.pop(key, None) is not None else 0

    async def eval(self, script: str, numkeys: int, *keys_and_args) -> int:
        """Эмуляция Redis EVAL для Lua-скрипта снятия блокировки по значению."""
        keys = list(keys_and_args[:numkeys])
        args = list(keys_and_args[numkeys:])
        if not keys or not args:
            return 0
        key, expected = keys[0], args[0]
        if self._store.get(key) == expected:
            del self._store[key]
            return 1
        return 0


# Проверяет, что блокировка захватывается и освобождается корректно.
@pytest.mark.asyncio
async def test_lock_acquired_and_released() -> None:
    fake_redis = FakeRedis()
    async with acquire_scan_lock(fake_redis, "profile-1"):
        assert "scan_lock:profile:profile-1" in fake_redis._store

    assert "scan_lock:profile:profile-1" not in fake_redis._store


# Проверяет, что повторная блокировка того же профиля вызывает ошибку.
@pytest.mark.asyncio
async def test_lock_rejects_concurrent_scan() -> None:
    fake_redis = FakeRedis()
    async with acquire_scan_lock(fake_redis, "profile-1"):
        with pytest.raises(ScanLockAcquisitionError, match="уже сканируется"):
            async with acquire_scan_lock(fake_redis, "profile-1"):
                pass


# Проверяет, что блокировки разных профилей не мешают друг другу.
@pytest.mark.asyncio
async def test_lock_allows_different_profiles() -> None:
    fake_redis = FakeRedis()
    async with acquire_scan_lock(fake_redis, "profile-1"):
        async with acquire_scan_lock(fake_redis, "profile-2"):
            assert "scan_lock:profile:profile-1" in fake_redis._store
            assert "scan_lock:profile:profile-2" in fake_redis._store

# -*- coding: utf-8 -*-
"""Юнит-тесты Волны 2/E: граница суток кабинета + загрузка TZ-оффсета из Redis."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.dashboard.cabinet_spend import cabinet_day_start_utc
from core.meta_api.account_tz import DEFAULT_OFFSET_HOURS, load_offset, load_offset_map


# UTC-кабинет: граница суток = полночь UTC того же дня.
def test_boundary_utc_offset_zero() -> None:
    now = datetime(2026, 6, 19, 14, 30, tzinfo=UTC)
    assert cabinet_day_start_utc(0.0, now) == datetime(2026, 6, 19, 0, 0, tzinfo=UTC)


# Калининград +2: полночь по локали = 22:00 UTC предыдущего дня.
def test_boundary_positive_offset() -> None:
    now = datetime(2026, 6, 19, 14, 30, tzinfo=UTC)
    # local = 16:30 19-го → local midnight = 00:00 19-го local = 22:00 18-го UTC
    assert cabinet_day_start_utc(2.0, now) == datetime(2026, 6, 18, 22, 0, tzinfo=UTC)


# Кабинет Hermosillo −7: ночь UTC попадает во «вчера» по локали кабинета.
def test_boundary_negative_offset() -> None:
    now = datetime(2026, 6, 19, 3, 0, tzinfo=UTC)
    # local = 20:00 18-го → local midnight = 00:00 18-го local = 07:00 18-го UTC
    assert cabinet_day_start_utc(-7.0, now) == datetime(2026, 6, 18, 7, 0, tzinfo=UTC)


# Дробный оффсет +5.5 (India): полночь по локали = 18:30 UTC предыдущего дня.
def test_boundary_fractional_offset() -> None:
    now = datetime(2026, 6, 19, 14, 30, tzinfo=UTC)
    assert cabinet_day_start_utc(5.5, now) == datetime(2026, 6, 18, 18, 30, tzinfo=UTC)


# Инвариант: для любого оффсета now ∈ [boundary, boundary + 24ч) — «сейчас» внутри текущих суток.
@pytest.mark.parametrize("offset", [-12.0, -7.0, 0.0, 2.0, 5.5, 13.0])
def test_now_within_cabinet_day(offset: float) -> None:
    now = datetime(2026, 6, 19, 11, 27, tzinfo=UTC)
    b = cabinet_day_start_utc(offset, now)
    assert b <= now < b + timedelta(days=1)


# Сразу после полуночи кабинета граница уже «сегодняшняя» (новые сутки начались).
def test_boundary_just_after_midnight() -> None:
    # offset 0, now = 00:01 UTC → граница = 00:00 того же дня (а не вчера).
    now = datetime(2026, 6, 19, 0, 1, tzinfo=UTC)
    assert cabinet_day_start_utc(0.0, now) == datetime(2026, 6, 19, 0, 0, tzinfo=UTC)


# --- account_tz: загрузка оффсета из Redis ---


class _FakeRedis:
    """Минимальный фейк Redis: get по словарю, decode_responses=True (строки)."""

    def __init__(self, store: dict[str, str]) -> None:
        self._store = store

    async def get(self, key: str):
        return self._store.get(key)


# Ключ в кэше → парсится в float оффсет.
@pytest.mark.asyncio
async def test_load_offset_from_cache() -> None:
    redis = _FakeRedis({"account_tz:123": "-7.0"})
    assert await load_offset(redis, "123") == -7.0


# Нет ключа → дефолт (UTC).
@pytest.mark.asyncio
async def test_load_offset_default_when_missing() -> None:
    redis = _FakeRedis({})
    assert await load_offset(redis, "999") == DEFAULT_OFFSET_HOURS


# Пустой account_id → дефолт без обращения к Redis.
@pytest.mark.asyncio
async def test_load_offset_empty_account() -> None:
    redis = _FakeRedis({"account_tz:": "5"})
    assert await load_offset(redis, "") == DEFAULT_OFFSET_HOURS


# Карта per-account: известные из кэша, неизвестные → дефолт.
@pytest.mark.asyncio
async def test_load_offset_map_mixed() -> None:
    redis = _FakeRedis({"account_tz:a": "2.0"})
    m = await load_offset_map(redis, ["a", "b"])
    assert m == {"a": 2.0, "b": DEFAULT_OFFSET_HOURS}


# Битое значение в кэше → дефолт (устойчивость).
@pytest.mark.asyncio
async def test_load_offset_corrupt_value() -> None:
    redis = _FakeRedis({"account_tz:x": "не-число"})
    assert await load_offset(redis, "x") == DEFAULT_OFFSET_HOURS


# --- maybe_refresh_account_tz: throttle с успехо-зависимым интервалом (фикс ревью) ---


class _FakeRedisRW:
    """Фейк Redis с set(ex, nx) + get для проверки throttle-логики."""

    def __init__(self) -> None:
        self.store: dict = {}

    async def set(self, key, value, *, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = (value, ex)
        return True

    async def get(self, key):
        x = self.store.get(key)
        return x[0] if x else None


# Успешный refresh (>0) → throttle продлевается на полный интервал.
@pytest.mark.asyncio
async def test_maybe_refresh_success_extends(monkeypatch) -> None:
    import core.meta_api.account_tz as m

    async def fake_refresh(_e, _r, _c):
        return 1

    monkeypatch.setattr(m, "refresh_account_tz_cache", fake_refresh)
    redis = _FakeRedisRW()
    ok = await m.maybe_refresh_account_tz(
        None, redis, None, min_interval_seconds=6000, retry_interval_seconds=60
    )
    assert ok is True
    assert redis.store[m._REFRESH_THROTTLE_KEY][1] == 6000


# Провал refresh (0 обновлено) → остаётся КОРОТКИЙ lock (повтор скоро, не виснет на 6ч).
@pytest.mark.asyncio
async def test_maybe_refresh_failure_keeps_short(monkeypatch) -> None:
    import core.meta_api.account_tz as m

    async def fake_refresh(_e, _r, _c):
        return 0

    monkeypatch.setattr(m, "refresh_account_tz_cache", fake_refresh)
    redis = _FakeRedisRW()
    ok = await m.maybe_refresh_account_tz(
        None, redis, None, min_interval_seconds=6000, retry_interval_seconds=60
    )
    assert ok is False
    assert redis.store[m._REFRESH_THROTTLE_KEY][1] == 60


# Пока throttle-ключ жив — refresh не зовётся повторно.
@pytest.mark.asyncio
async def test_maybe_refresh_throttled(monkeypatch) -> None:
    import core.meta_api.account_tz as m

    calls = []

    async def fake_refresh(_e, _r, _c):
        calls.append(1)
        return 1

    monkeypatch.setattr(m, "refresh_account_tz_cache", fake_refresh)
    redis = _FakeRedisRW()
    await m.maybe_refresh_account_tz(None, redis, None)
    second = await m.maybe_refresh_account_tz(None, redis, None)
    assert second is False
    assert len(calls) == 1

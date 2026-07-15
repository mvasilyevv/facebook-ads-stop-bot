# -*- coding: utf-8 -*-
"""Unit: MID-8 — offset НЕ подтверждается для упавшего handle_update.

Раньше offset двигался ДО handle_update и безусловно → упавший callback (money-кнопка
dis:/ereco: под алертом) терялся навсегда (at-most-once): Telegram больше не переотдавал
update. Фикс: offset двигаем только за успешно обработанные; упавший (не-ядовитый) update
оставляет offset позади → переобработка на следующем poll (at-least-once). Ядовитый update
(падает _MAX_UPDATE_ATTEMPTS раз) скипается с ERROR-логом, чтобы не заморозить очередь.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import apps.telegram_poller.main as poller


def _upd(update_id: int) -> dict:
    return {"update_id": update_id, "callback_query": {"data": f"dis:{update_id}"}}


@pytest.fixture(autouse=True)
def _clear_inmem():
    """Сброс in-memory счётчика попыток между тестами (модульный global)."""
    poller._inmem_update_fail_counts.clear()
    yield
    poller._inmem_update_fail_counts.clear()


# Все updates обработаны успешно → offset двигается до последнего update_id.
@pytest.mark.asyncio
async def test_all_success_advances_offset(monkeypatch) -> None:
    monkeypatch.setattr(poller, "handle_update", AsyncMock())
    new_offset = await poller._process_updates_batch(
        [_upd(10), _upd(11), _upd(12)],
        engine=object(),
        client=object(),
        redis_pubsub=object(),
        fail_redis=None,
        offset=9,
    )
    assert new_offset == 12


# Упавший handle_update → offset НЕ двигается за упавший update (переобработается).
@pytest.mark.asyncio
async def test_failed_update_does_not_advance_offset(monkeypatch) -> None:
    monkeypatch.setattr(poller, "handle_update", AsyncMock(side_effect=RuntimeError("boom")))
    new_offset = await poller._process_updates_batch(
        [_upd(10)],
        engine=object(),
        client=object(),
        redis_pubsub=object(),
        fail_redis=None,
        offset=9,
    )
    # offset остался на 9 (до упавшего 10) → Telegram переотдаст update 10.
    assert new_offset == 9


# Батч: первый ok, второй падает → offset двигается только до первого, хвост переобработается.
@pytest.mark.asyncio
async def test_batch_stops_at_first_failure(monkeypatch) -> None:
    async def handler(*, engine, client, update, redis, **_deps):
        if update["update_id"] == 11:
            raise RuntimeError("boom on 11")

    monkeypatch.setattr(poller, "handle_update", handler)
    new_offset = await poller._process_updates_batch(
        [_upd(10), _upd(11), _upd(12)],
        engine=object(),
        client=object(),
        redis_pubsub=object(),
        fail_redis=None,
        offset=9,
    )
    # 10 обработан (offset=10), 11 упал → прерываем батч, 11 и 12 переобработаются.
    assert new_offset == 10


# Ядовитый update: попытки 1 и 2 держат offset, 3-я (count == _MAX_UPDATE_ATTEMPTS=3)
# → скип с ERROR-логом, offset уходит за него (очередь не заморожена битым update).
@pytest.mark.asyncio
async def test_poison_update_skipped_after_max_attempts(monkeypatch) -> None:
    monkeypatch.setattr(poller, "handle_update", AsyncMock(side_effect=RuntimeError("poison")))
    assert poller._MAX_UPDATE_ATTEMPTS == 3

    offset = 9
    # Попытки 1 и 2 (count < лимита): offset НЕ двигается — update переотдаётся Telegram'ом.
    for attempt in range(1, poller._MAX_UPDATE_ATTEMPTS):
        offset = await poller._process_updates_batch(
            [_upd(10)],
            engine=object(),
            client=object(),
            redis_pubsub=object(),
            fail_redis=None,
            offset=offset,
        )
        assert offset == 9, f"попытка {attempt}: offset не должен двигаться за упавший update"

    # 3-я попытка: count становится 3 == _MAX_UPDATE_ATTEMPTS → ядовитый → скип навсегда.
    offset = await poller._process_updates_batch(
        [_upd(10)],
        engine=object(),
        client=object(),
        redis_pubsub=object(),
        fail_redis=None,
        offset=offset,
    )
    assert offset == 10, "ядовитый update после лимита попыток должен быть скипнут (offset за него)"


# Успешная обработка сбрасывает счётчик попыток (флапнувший update восстановился).
@pytest.mark.asyncio
async def test_success_clears_failure_counter(monkeypatch) -> None:
    calls = {"n": 0}

    async def flaky(*, engine, client, update, redis, **_deps):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        # вторая попытка — успех

    monkeypatch.setattr(poller, "handle_update", flaky)

    # 1-я попытка падает → offset не двигается, счётчик=1.
    offset = await poller._process_updates_batch(
        [_upd(10)],
        engine=object(),
        client=object(),
        redis_pubsub=object(),
        fail_redis=None,
        offset=9,
    )
    assert offset == 9
    assert poller._inmem_update_fail_counts.get(10) == 1

    # 2-я попытка успешна → offset двигается, счётчик очищен.
    offset = await poller._process_updates_batch(
        [_upd(10)],
        engine=object(),
        client=object(),
        redis_pubsub=object(),
        fail_redis=None,
        offset=9,
    )
    assert offset == 10
    assert 10 not in poller._inmem_update_fail_counts


# Redis-счётчик: incr вызывается, expire ставится на первой попытке.
@pytest.mark.asyncio
async def test_bump_uses_redis_incr_and_expire() -> None:
    redis = AsyncMock()
    redis.incr = AsyncMock(return_value=1)
    redis.expire = AsyncMock()

    count = await poller._bump_update_failure(redis, 555)

    assert count == 1
    redis.incr.assert_awaited_once_with(f"{poller._UPDATE_FAIL_KEY_PREFIX}555")
    redis.expire.assert_awaited_once_with(
        f"{poller._UPDATE_FAIL_KEY_PREFIX}555", poller._UPDATE_FAIL_TTL_SECONDS
    )


# Redis лёг на incr → in-memory fallback (не залипаем, secondary cap работает).
@pytest.mark.asyncio
async def test_bump_falls_back_to_inmemory_on_redis_error() -> None:
    redis = AsyncMock()
    redis.incr = AsyncMock(side_effect=RuntimeError("redis down"))

    c1 = await poller._bump_update_failure(redis, 777)
    c2 = await poller._bump_update_failure(redis, 777)

    assert c1 == 1 and c2 == 2, "in-memory fallback должен считать попытки при сбое Redis"

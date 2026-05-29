# -*- coding: utf-8 -*-
"""Контрактный тест: воркеры пишут heartbeat-ключи совпадающие с EXPECTED_WORKERS.

Проверяет что после фикса health_watchdog НЕ будет ложных алертов:
- каждый EXPECTED-воркер периодически пишет worker:heartbeat:<name>
- имя в ключе ТОЧНО совпадает с записью в EXPECTED_WORKERS
- watchdog НЕ алертит когда ключ присутствует (should_alert=False)
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

# Используем fakeredis чтобы не требовать живой Redis
try:
    from fakeredis.aioredis import FakeRedis  # type: ignore

    _FAKEREDIS_OK = True
except ImportError:
    _FAKEREDIS_OK = False

pytestmark = pytest.mark.skipif(
    not _FAKEREDIS_OK,
    reason="fakeredis не установлен — пропускаю heartbeat contract тесты",
)


# ====================== фикстуры ======================


@pytest_asyncio.fixture
async def fake_redis():
    """Чистый fakeredis без персистентности — изолирует тест от живого Redis."""
    r = FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


# ====================== вспомогательные функции ======================


async def _run_one_heartbeat_cycle(heartbeat_fn, redis_client) -> None:
    """Запускает heartbeat и ждём одну запись в Redis.

    Стратегия: запускаем heartbeat_loop как таск, даём ему выполниться
    (asyncio.sleep(0) передаёт управление), потом отменяем.
    Так гарантируем хотя бы одну запись — независимо от реализации цикла.
    """
    stop = asyncio.Event()
    task = asyncio.create_task(heartbeat_fn(redis_client, stop))
    # Несколько yield'ов чтобы таск успел записать первый heartbeat
    for _ in range(5):
        await asyncio.sleep(0)
    stop.set()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ====================== канонический набор имён EXPECTED ======================
# Это единственный источник истины — копируется из health_watchdog.DEFAULT_EXPECTED_WORKERS.
# При изменении EXPECTED_WORKERS в watchdog — обновлять здесь тоже.
_EXPECTED_WORKERS = [
    "observer",
    "disable",
    "enable",
    "telegram_poller",
    "cleanup",
    "reconciler",
    "meta_api",
]


# ====================== параметризованный анти-регресс ======================


@pytest.mark.parametrize("worker_name", _EXPECTED_WORKERS)
def test_expected_worker_name_is_canonical(worker_name: str) -> None:
    """Каждое имя в EXPECTED_WORKERS — это то имя, под которым воркер пишет heartbeat.

    Тест явно документирует контракт: имена без суффикса _worker.
    Если кто-то переименует воркер — тест упадёт и напомнит обновить EXPECTED.
    """
    # Имена не должны содержать суффикс _worker (это было причиной бага)
    assert not worker_name.endswith("_worker"), (
        f"EXPECTED имя '{worker_name}' содержит '_worker' — "
        "это вызовет несовпадение с heartbeat-ключом воркера"
    )
    # Имена должны быть непустыми строками без пробелов
    assert worker_name and worker_name == worker_name.strip()


def test_expected_workers_match_watchdog_default() -> None:
    """EXPECTED_WORKERS в тесте совпадают с DEFAULT_EXPECTED_WORKERS в watchdog.

    При изменении DEFAULT_EXPECTED_WORKERS в watchdog тест упадёт и напомнит
    обновить контракт.
    """
    from apps.health_watchdog.main import DEFAULT_EXPECTED_WORKERS, parse_expected_workers

    watchdog_names = parse_expected_workers(DEFAULT_EXPECTED_WORKERS)
    assert set(_EXPECTED_WORKERS) == set(watchdog_names), (
        f"Несовпадение: тест ожидает {sorted(_EXPECTED_WORKERS)}, "
        f"watchdog знает {sorted(watchdog_names)}"
    )


# ====================== observer heartbeat ======================


@pytest.mark.asyncio
async def test_observer_writes_heartbeat(fake_redis) -> None:
    """observer_worker пишет worker:heartbeat:observer при вызове heartbeat_loop."""
    from apps.observer_worker.main import HEARTBEAT_KEY, heartbeat_loop

    await _run_one_heartbeat_cycle(heartbeat_loop, fake_redis)

    value = await fake_redis.get(HEARTBEAT_KEY)
    assert value is not None, "observer не записал heartbeat-ключ"
    assert HEARTBEAT_KEY == "worker:heartbeat:observer"


@pytest.mark.asyncio
async def test_observer_heartbeat_key_in_expected(fake_redis) -> None:
    """Ключ observer'а — имя из EXPECTED_WORKERS."""
    from apps.observer_worker.main import WORKER_NAME

    assert WORKER_NAME in _EXPECTED_WORKERS, (
        f"observer WORKER_NAME={WORKER_NAME!r} не входит в EXPECTED_WORKERS"
    )


# ====================== disable/enable heartbeat через toggle_executor ======================


async def _run_toggle_heartbeat_one_cycle(task_type: str, redis_client) -> None:
    """Запускает _heartbeat_loop из toggle_executor один раз и гарантирует запись."""
    from core.tasks.toggle_executor import _heartbeat_loop

    stop = asyncio.Event()
    task = asyncio.create_task(_heartbeat_loop(redis_client, task_type, stop))
    for _ in range(5):
        await asyncio.sleep(0)
    stop.set()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_disable_writes_heartbeat(fake_redis) -> None:
    """run_toggle_loop с task_type='disable' пишет worker:heartbeat:disable."""
    await _run_toggle_heartbeat_one_cycle("disable", fake_redis)

    value = await fake_redis.get("worker:heartbeat:disable")
    assert value is not None, "disable worker не записал heartbeat-ключ"


@pytest.mark.asyncio
async def test_enable_writes_heartbeat(fake_redis) -> None:
    """run_toggle_loop с task_type='enable' пишет worker:heartbeat:enable."""
    await _run_toggle_heartbeat_one_cycle("enable", fake_redis)

    value = await fake_redis.get("worker:heartbeat:enable")
    assert value is not None, "enable worker не записал heartbeat-ключ"


@pytest.mark.asyncio
async def test_toggle_heartbeat_key_matches_task_type(fake_redis) -> None:
    """Ключ heartbeat в toggle_executor = worker:heartbeat:<task_type> — имя из EXPECTED."""
    for task_type in ("disable", "enable"):
        await _run_toggle_heartbeat_one_cycle(task_type, fake_redis)
        value = await fake_redis.get(f"worker:heartbeat:{task_type}")
        assert value is not None, f"toggle heartbeat не записан для task_type={task_type!r}"
        assert task_type in _EXPECTED_WORKERS


# ====================== telegram_poller heartbeat ======================


@pytest.mark.asyncio
async def test_telegram_poller_writes_heartbeat(fake_redis) -> None:
    """telegram_poller пишет worker:heartbeat:telegram_poller при вызове heartbeat_loop."""
    from apps.telegram_poller.main import HEARTBEAT_KEY, WORKER_NAME, heartbeat_loop

    await _run_one_heartbeat_cycle(heartbeat_loop, fake_redis)

    value = await fake_redis.get(HEARTBEAT_KEY)
    assert value is not None, "telegram_poller не записал heartbeat-ключ"
    assert WORKER_NAME in _EXPECTED_WORKERS
    assert HEARTBEAT_KEY == f"worker:heartbeat:{WORKER_NAME}"


# ====================== cleanup heartbeat ======================


@pytest.mark.asyncio
async def test_cleanup_writes_heartbeat(fake_redis) -> None:
    """cleanup_worker пишет worker:heartbeat:cleanup при вызове heartbeat_loop."""
    from apps.cleanup_worker.main import HEARTBEAT_KEY, WORKER_NAME, heartbeat_loop

    await _run_one_heartbeat_cycle(heartbeat_loop, fake_redis)

    value = await fake_redis.get(HEARTBEAT_KEY)
    assert value is not None, "cleanup_worker не записал heartbeat-ключ"
    assert WORKER_NAME in _EXPECTED_WORKERS
    assert HEARTBEAT_KEY == f"worker:heartbeat:{WORKER_NAME}"


# ====================== reconciler heartbeat ======================


@pytest.mark.asyncio
async def test_reconciler_writes_heartbeat(fake_redis) -> None:
    """reconciler_worker пишет worker:heartbeat:reconciler при вызове heartbeat_loop."""
    from apps.reconciler_worker.main import HEARTBEAT_KEY, WORKER_NAME, heartbeat_loop

    await _run_one_heartbeat_cycle(heartbeat_loop, fake_redis)

    value = await fake_redis.get(HEARTBEAT_KEY)
    assert value is not None, "reconciler_worker не записал heartbeat-ключ"
    assert WORKER_NAME in _EXPECTED_WORKERS
    assert HEARTBEAT_KEY == f"worker:heartbeat:{WORKER_NAME}"


# ====================== watchdog не алертит когда ключ есть ======================


@pytest.mark.asyncio
async def test_watchdog_no_false_alert_when_heartbeat_present(fake_redis) -> None:
    """health_watchdog НЕ шлёт алерт когда воркер пишет свой heartbeat.

    До фикса: observer писал только observer:runtime, watchdog искал
    worker:heartbeat:observer → ключа нет → ложный алерт «observer мёртв».
    После фикса: ключ есть → should_alert=False → алерт не отправляется.
    """
    from apps.health_watchdog.main import should_alert

    # Пишем heartbeat для каждого EXPECTED-воркера
    for name in _EXPECTED_WORKERS:
        await fake_redis.set(f"worker:heartbeat:{name}", "alive", ex=60)

    # Для каждого воркера проверяем should_alert → False
    for name in _EXPECTED_WORKERS:
        hb_value = await fake_redis.get(f"worker:heartbeat:{name}")
        dedup_value = await fake_redis.get(f"health:alerted:{name}")
        result = should_alert(hb_value, dedup_value)
        assert result is False, (
            f"should_alert вернул True для '{name}' хотя heartbeat присутствует — ложный алерт!"
        )


@pytest.mark.asyncio
async def test_watchdog_alerts_when_heartbeat_missing(fake_redis) -> None:
    """health_watchdog ШЛЁТ алерт когда heartbeat истёк (ключа нет).

    Убеждаемся что механизм алертинга работает — только что добавленные
    heartbeat-функции его и предотвращают.
    """
    from apps.health_watchdog.main import should_alert

    # Ключей нет (имитация истёкшего TTL)
    hb_value = await fake_redis.get("worker:heartbeat:observer")
    dedup_value = await fake_redis.get("health:alerted:observer")

    result = should_alert(hb_value, dedup_value)
    assert result is True, "should_alert должен вернуть True когда heartbeat отсутствует"


# ====================== meta_api heartbeat (уже работал — регрессия) ======================


@pytest.mark.asyncio
async def test_meta_api_heartbeat_key_is_correct() -> None:
    """meta_api_worker пишет worker:heartbeat:meta_api — это уже работало.

    Проверяем что имя не изменилось при наших правках (регрессионный тест).
    """
    from apps.meta_api_worker.main import HEARTBEAT_KEY, WORKER_NAME

    assert WORKER_NAME == "meta_api"
    assert HEARTBEAT_KEY == "worker:heartbeat:meta_api"
    assert WORKER_NAME in _EXPECTED_WORKERS

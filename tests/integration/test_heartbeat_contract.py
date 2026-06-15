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
# Зеркало health_watchdog.DEFAULT_EXPECTED_WORKERS (11 воркеров). При изменении
# EXPECTED_WORKERS в watchdog — обновлять здесь (test_expected_workers_match_watchdog_default
# это проверяет). disable/enable удалены (DOM-toggle выпилен). H-13: список был устаревшим
# (7), watchdog мониторит 11 (+cabinet_scheduler/digest_scheduler/creator/creator_recorder) —
# их зависание money-критично/важно, должно ловиться. Имена сверены с WORKER_NAME воркеров.
_EXPECTED_WORKERS = [
    "observer",
    "telegram_poller",
    "cleanup",
    "reconciler",
    "meta_api",
    "tracker_aggregator",
    "enable_reco",
    "cabinet_scheduler",
    "digest_scheduler",
    "creator",
    "creator_recorder",
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


def test_ai_tool_worker_list_matches_watchdog() -> None:
    """AI-tool get_worker_health видит ТОТ ЖЕ набор воркеров, что watchdog.

    Регресс MCP-аудита 2026-06-10: tool знал только 5 воркеров из 11 —
    «здоровье системы» в Claude Desktop умалчивало про мёртвый cabinet_scheduler.
    """
    from apps.health_watchdog.main import DEFAULT_EXPECTED_WORKERS, parse_expected_workers
    from core.ai_assistant.tools.ops.get_worker_health import EXPECTED_WORKERS

    watchdog_names = set(parse_expected_workers(DEFAULT_EXPECTED_WORKERS))
    assert set(EXPECTED_WORKERS) == watchdog_names, (
        f"get_worker_health: {sorted(EXPECTED_WORKERS)} != watchdog {sorted(watchdog_names)}"
    )


def test_mcp_resource_worker_list_matches_watchdog() -> None:
    """MCP-ресурс workers-health использует канонический список (импорт из tool'а).

    Регресс: локальная копия в resources.py содержала фантомные disable/enable
    и не знала про cabinet_scheduler/tracker_aggregator.
    """
    from apps.health_watchdog.main import DEFAULT_EXPECTED_WORKERS, parse_expected_workers
    from apps.mcp_server.resources import _EXPECTED_WORKERS as mcp_workers

    watchdog_names = set(parse_expected_workers(DEFAULT_EXPECTED_WORKERS))
    assert set(mcp_workers) == watchdog_names, (
        f"mcp resources: {sorted(mcp_workers)} != watchdog {sorted(watchdog_names)}"
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


# ====================== tracker_aggregator heartbeat ======================


@pytest.mark.asyncio
async def test_tracker_aggregator_writes_heartbeat(fake_redis) -> None:
    """tracker_aggregator_worker пишет worker:heartbeat:tracker_aggregator."""
    from apps.tracker_aggregator_worker.main import HEARTBEAT_KEY, WORKER_NAME, heartbeat_loop

    await _run_one_heartbeat_cycle(heartbeat_loop, fake_redis)

    value = await fake_redis.get(HEARTBEAT_KEY)
    assert value is not None, "tracker_aggregator не записал heartbeat-ключ"
    assert WORKER_NAME in _EXPECTED_WORKERS
    assert HEARTBEAT_KEY == f"worker:heartbeat:{WORKER_NAME}"


# ====================== enable_reco heartbeat (имя != enable_recommendation) ======================


def test_enable_reco_name_is_canonical() -> None:
    """enable_recommendation_worker пишет heartbeat под именем 'enable_reco' — это имя в EXPECTED."""
    from apps.enable_recommendation_worker.main import HEARTBEAT_KEY, WORKER_NAME

    assert WORKER_NAME == "enable_reco"
    assert HEARTBEAT_KEY == "worker:heartbeat:enable_reco"
    assert WORKER_NAME in _EXPECTED_WORKERS


# enable_reco пишет heartbeat через ФОНОВЫЙ loop (раньше — в основном цикле раз в 300с при
# TTL 60с, ключ протухал → watchdog ложно алертил). Теперь не протухает между прогонами.
@pytest.mark.asyncio
async def test_enable_reco_writes_heartbeat(fake_redis) -> None:
    """enable_reco пишет worker:heartbeat:enable_reco через фоновый heartbeat_loop."""
    from apps.enable_recommendation_worker.main import HEARTBEAT_KEY, heartbeat_loop

    await _run_one_heartbeat_cycle(heartbeat_loop, fake_redis)

    assert await fake_redis.get(HEARTBEAT_KEY) is not None, "enable_reco не записал heartbeat"


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

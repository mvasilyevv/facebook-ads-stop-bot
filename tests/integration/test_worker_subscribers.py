# -*- coding: utf-8 -*-
"""Интеграционные тесты: worker'ы реагируют на Redis pubsub-сигналы.

Стратегия контракта (не тавтология):
- Для disable_worker: запускаем РЕАЛЬНЫЙ main_loop с fakeredis + stop_event,
  публикуем CHANNEL_RESTART → проверяем что stop_event.is_set().
  Это E2E: реальный код воркера регистрирует реальный handler, реальный publish.
- Для observer: используем spy на RedisPubSubListener.register, запускаем
  РЕАЛЬНЫЙ main_loop (с заглушкой gate/db), проверяем что воркер зарегистрировал
  правильные каналы. Это контракт «воркер связал канал↔handler» (не тавтология:
  если воркер перепутает каналы, spy поймает).
- cabinet_day pubsub-плечо: честный skip+TODO, т.к. handler существует в коде
  но не делает ничего (TODO: archive-логика). Тест зафиксирован через маркер.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict

import pytest

pytest.importorskip("fakeredis")


def _make_fake_redis():
    """Создаёт in-memory FakeRedis с decode_responses=True."""
    import fakeredis.aioredis  # type: ignore

    return fakeredis.aioredis.FakeRedis(decode_responses=True)


# ====================== Тесты disable worker (E2E) ======================


# disable_worker: реальный main_loop — CHANNEL_RESTART доставляет stop через E2E.
@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_disable_worker_restart_contract_e2e(monkeypatch) -> None:
    """Реальный main_loop disable_worker: publish CHANNEL_RESTART → stop_event.set().

    Это E2E контракт: реальный код воркера (apps/disable_worker/main.py) регистрирует
    реальный _on_restart handler, реальный fakeredis доставляет сообщение.
    В отличие от старого теста — мы НЕ определяем handler локально.
    Проверяем поведение РЕАЛЬНОГО handler'а воркера.
    """
    from apps.disable_worker.main import CHANNEL_RESTART, main_loop

    redis_client = _make_fake_redis()
    stop_event = asyncio.Event()

    # Монкипатч _get_database_url чтобы воркер не пытался подключиться к прод-БД.
    # toggle_loop сразу выходит т.к. stop_event будет установлен до получения task'а.
    monkeypatch.setattr(
        "apps.telegram_poller.main._get_database_url",
        lambda: "postgresql+asyncpg://user:pass@127.0.0.1:9999/nonexistent",
    )

    async def _fake_redis_factory():
        return redis_client

    # Запускаем реальный main_loop в фоне.
    # toggle_loop упадёт при попытке подключиться к БД — это ок, нам важен listener.
    loop_task = asyncio.create_task(
        main_loop(
            redis_factory=_fake_redis_factory,
            stop_event=stop_event,
        )
    )

    # Даём listener'у время подписаться (он инициализируется в main_loop).
    await asyncio.sleep(0.3)

    # Публикуем CHANNEL_RESTART от имени publisher'а (API endpoint).
    await redis_client.publish(CHANNEL_RESTART, json.dumps({"reason": "api_request"}))

    # Ждём stop_event от РЕАЛЬНОГО handler'а воркера.
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pytest.fail(
            "stop_event не выставлен за 2 сек — "
            "CHANNEL_RESTART не зарегистрирован или handler не ставит stop_event"
        )

    # Ожидаем завершения main_loop (он выйдет после stop или DB-ошибки).
    loop_task.cancel()
    try:
        await loop_task
    except (asyncio.CancelledError, Exception):
        pass
    finally:
        await redis_client.aclose()

    # Реальный handler воркера выставил stop_event при получении CHANNEL_RESTART.
    assert stop_event.is_set(), "stop_event должен быть set после restart-сигнала"


# ====================== Тесты observer worker (spy на register) ======================


def _make_observer_channel_spy():
    """Создаёт spy-класс поверх RedisPubSubListener.register.

    Записывает все (channel, handler) пары зарегистрированные воркером.
    Оригинальный register вызывается нетронутым — spy не меняет поведение.
    """
    registered: dict[str, list] = defaultdict(list)
    original_register = None

    class _SpyListener:
        def __init__(self, orig_class):
            self._orig = orig_class
            self.registered = registered

        def patch_register(self, instance, channel, handler):
            """Перехватываем register, записываем (channel, handler_name), вызываем оригинал."""
            registered[channel].append(getattr(handler, "__name__", repr(handler)))
            return original_register(instance, channel, handler)

    return _SpyListener, registered


# observer worker: spy проверяет что CHANNEL_TRIGGER зарегистрирован с handler'ом.
@pytest.mark.asyncio
@pytest.mark.timeout(8)
async def test_observer_registers_trigger_channel(monkeypatch) -> None:
    """Реальный main_loop observer регистрирует CHANNEL_TRIGGER → handler.

    Spy на RedisPubSubListener.register фиксирует все вызовы register изнутри воркера.
    Проверяем что воркер сам вызвал register(CHANNEL_TRIGGER, _on_trigger).
    В отличие от старого теста — handler НЕ определяется в тесте, мы не трогаем логику.
    """
    from apps.observer_worker.main import CHANNEL_TRIGGER, main_loop

    registered_calls: dict[str, list[str]] = defaultdict(list)

    # Монкипатч БД — воркер не должен подключаться к прод-БД.
    monkeypatch.setattr(
        "apps.telegram_poller.main._get_database_url",
        lambda: "postgresql+asyncpg://user:pass@127.0.0.1:9999/nonexistent",
    )

    # Spy: оборачиваем RedisPubSubListener.register чтобы записать вызовы.
    from core.control.pubsub_listener import RedisPubSubListener

    original_register = RedisPubSubListener.register

    def _spy_register(self, channel: str, handler) -> None:
        registered_calls[channel].append(getattr(handler, "__name__", repr(handler)))
        return original_register(self, channel, handler)

    monkeypatch.setattr(RedisPubSubListener, "register", _spy_register)

    redis_client = _make_fake_redis()

    async def _fake_redis_factory():
        return redis_client

    # gate_factory немедленно падает → main_loop спит 10s между попытками
    # Нам достаточно что listener зарегистрирован ДО первого gate-attempt.
    async def _failing_gate_factory():
        raise ConnectionError("тест: gate недоступен")

    # should_continue: после регистрации listener'а останавливаем цикл.
    stop_after_register = asyncio.Event()

    def _should_continue() -> bool:
        return not stop_after_register.is_set()

    loop_task = asyncio.create_task(
        main_loop(
            gate_factory=_failing_gate_factory,
            redis_factory=_fake_redis_factory,
            should_continue=_should_continue,
        )
    )

    # Даём воркеру время запуститься и зарегистрировать listener'а.
    await asyncio.sleep(0.5)
    stop_after_register.set()

    try:
        await asyncio.wait_for(loop_task, timeout=3.0)
    except (asyncio.TimeoutError, Exception):
        loop_task.cancel()
        try:
            await loop_task
        except (asyncio.CancelledError, Exception):
            pass

    await redis_client.aclose()

    # Воркер зарегистрировал CHANNEL_TRIGGER — контракт publisher↔subscriber.
    assert CHANNEL_TRIGGER in registered_calls, (
        f"CHANNEL_TRIGGER '{CHANNEL_TRIGGER}' не зарегистрирован воркером. "
        f"Зарегистрированы: {list(registered_calls.keys())}"
    )
    assert len(registered_calls[CHANNEL_TRIGGER]) >= 1, (
        f"Ожидался handler для CHANNEL_TRIGGER, получено: {registered_calls[CHANNEL_TRIGGER]}"
    )


# observer worker: spy проверяет что CHANNEL_RESTART зарегистрирован с handler'ом.
@pytest.mark.asyncio
@pytest.mark.timeout(8)
async def test_observer_registers_restart_channel(monkeypatch) -> None:
    """Реальный main_loop observer регистрирует CHANNEL_RESTART → handler.

    Симметрично test_observer_registers_trigger_channel но для канала restart.
    """
    from apps.observer_worker.main import CHANNEL_RESTART, main_loop

    registered_calls: dict[str, list[str]] = defaultdict(list)

    monkeypatch.setattr(
        "apps.telegram_poller.main._get_database_url",
        lambda: "postgresql+asyncpg://user:pass@127.0.0.1:9999/nonexistent",
    )

    from core.control.pubsub_listener import RedisPubSubListener

    original_register = RedisPubSubListener.register

    def _spy_register(self, channel: str, handler) -> None:
        registered_calls[channel].append(getattr(handler, "__name__", repr(handler)))
        return original_register(self, channel, handler)

    monkeypatch.setattr(RedisPubSubListener, "register", _spy_register)

    redis_client = _make_fake_redis()

    async def _fake_redis_factory():
        return redis_client

    async def _failing_gate_factory():
        raise ConnectionError("тест: gate недоступен")

    stop_flag = asyncio.Event()

    def _should_continue() -> bool:
        return not stop_flag.is_set()

    loop_task = asyncio.create_task(
        main_loop(
            gate_factory=_failing_gate_factory,
            redis_factory=_fake_redis_factory,
            should_continue=_should_continue,
        )
    )

    await asyncio.sleep(0.5)
    stop_flag.set()

    try:
        await asyncio.wait_for(loop_task, timeout=3.0)
    except (asyncio.TimeoutError, Exception):
        loop_task.cancel()
        try:
            await loop_task
        except (asyncio.CancelledError, Exception):
            pass

    await redis_client.aclose()

    assert CHANNEL_RESTART in registered_calls, (
        f"CHANNEL_RESTART '{CHANNEL_RESTART}' не зарегистрирован воркером. "
        f"Зарегистрированы: {list(registered_calls.keys())}"
    )


# ====================== Тесты cabinet_day ======================


# cabinet_day pubsub-плечо: handler зарегистрирован, но логика — TODO (архивирование).
@pytest.mark.asyncio
@pytest.mark.timeout(8)
async def test_observer_registers_cabinet_day_channel(monkeypatch) -> None:
    """Реальный main_loop observer регистрирует CHANNEL_CABINET_DAY → handler.

    Контракт: воркер слушает cabinet_day. Реакция — TODO: archive-логика.
    Сейчас handler только логирует (см. apps/observer_worker/main.py _on_cabinet_day).

    Тест проверяет что канал зарегистрирован — если в будущем добавится archive-логика,
    E2E-тест должен дополнить этот до полного контракта с реальным publish+effect.
    """
    from apps.observer_worker.main import CHANNEL_CABINET_DAY, main_loop

    registered_calls: dict[str, list[str]] = defaultdict(list)

    monkeypatch.setattr(
        "apps.telegram_poller.main._get_database_url",
        lambda: "postgresql+asyncpg://user:pass@127.0.0.1:9999/nonexistent",
    )

    from core.control.pubsub_listener import RedisPubSubListener

    original_register = RedisPubSubListener.register

    def _spy_register(self, channel: str, handler) -> None:
        registered_calls[channel].append(getattr(handler, "__name__", repr(handler)))
        return original_register(self, channel, handler)

    monkeypatch.setattr(RedisPubSubListener, "register", _spy_register)

    redis_client = _make_fake_redis()

    async def _fake_redis_factory():
        return redis_client

    async def _failing_gate_factory():
        raise ConnectionError("тест: gate недоступен")

    stop_flag = asyncio.Event()

    def _should_continue() -> bool:
        return not stop_flag.is_set()

    loop_task = asyncio.create_task(
        main_loop(
            gate_factory=_failing_gate_factory,
            redis_factory=_fake_redis_factory,
            should_continue=_should_continue,
        )
    )

    await asyncio.sleep(0.5)
    stop_flag.set()

    try:
        await asyncio.wait_for(loop_task, timeout=3.0)
    except (asyncio.TimeoutError, Exception):
        loop_task.cancel()
        try:
            await loop_task
        except (asyncio.CancelledError, Exception):
            pass

    await redis_client.aclose()

    # Канал зарегистрирован (контракт публишера ↔ воркера).
    assert CHANNEL_CABINET_DAY in registered_calls, (
        f"CHANNEL_CABINET_DAY '{CHANNEL_CABINET_DAY}' не зарегистрирован. "
        f"Зарегистрированы: {list(registered_calls.keys())}. "
        "Если канал удалён — удалить и этот тест."
    )

    # TODO (cabinet_day E2E): когда archive-логика реализована (_on_cabinet_day перестанет
    # быть no-op), добавить полный E2E тест: реальный publish → проверить что archiving
    # выполнен (например запись в cabinet_day_archives). Текущий handler только логирует.


# ====================== Тест: handler_exception не ломает loop ======================


# Исключение в handler не ломает listener-loop — следующий сигнал обрабатывается.
@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_handler_exception_does_not_crash_loop() -> None:
    from core.control.pubsub_listener import RedisPubSubListener

    redis_client = _make_fake_redis()
    channel = "test:crash:channel"
    good_calls: list[int] = []

    async def crashing_handler(_payload: dict) -> None:
        raise ValueError("намеренная ошибка в handler'е")

    async def counting_handler(payload: dict) -> None:
        good_calls.append(payload.get("n", 0))

    listener = RedisPubSubListener(redis_client, [channel])
    listener.register(channel, crashing_handler)
    listener.register(channel, counting_handler)

    task = asyncio.create_task(listener.run_forever())
    await asyncio.sleep(0.1)

    # Публикуем два сообщения — оба должны дойти до counting_handler.
    await redis_client.publish(channel, json.dumps({"n": 1}))
    await redis_client.publish(channel, json.dumps({"n": 2}))
    await asyncio.sleep(0.4)

    await listener.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        await redis_client.aclose()

    # counting_handler получил оба сообщения, несмотря на crashing_handler.
    assert sorted(good_calls) == [1, 2]

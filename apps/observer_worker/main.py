# -*- coding: utf-8 -*-
"""Observer worker main loop.

Цикл:
1. load_observer_config + check is_scanning_enabled
2. INSERT в scan_runs → scan_id
3. gate.run_scan_cycle() → ScanResult.rows
4. process_scan_rows(...) → метрики/FSM/outbox
5. UPDATE scan_runs финальным результатом
6. Redis heartbeat
7. Sleep interval_seconds + jitter

Gate инжектируется (паттерн как у toggle_workers): в проде это BrowserAgentClient,
в тестах — fake который возвращает заранее подготовленные ScannedAdRow.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from apps.telegram_poller.main import _get_database_url
from core.observer.pipeline import CycleResult, process_scan_rows
from core.observer.queries import load_observer_config
from core.scanner.models import ScannedAdRow

logger = logging.getLogger(__name__)

# Heartbeat — имя ДОЛЖНО совпадать с EXPECTED_WORKERS в health_watchdog.
WORKER_NAME = "observer"
HEARTBEAT_KEY = f"worker:heartbeat:{WORKER_NAME}"
HEARTBEAT_TTL_SECONDS = 60

# Управляющие каналы observer'а.
CHANNEL_TRIGGER = "fb_agent:observer:trigger"  # форс-скан вне расписания
CHANNEL_CABINET_DAY = "fb_agent:observer:cabinet_day"  # сигнал нового кабинетного дня
CHANNEL_RESTART = "fb_agent:worker:restart:observer"  # graceful restart


@dataclass
class ScanCycleOutput:
    """То что вернул scanner gate за один цикл."""

    rows: list[ScannedAdRow]
    total_passes: int = 0
    duration_seconds: float = 0.0
    empty_reason: str | None = None
    warnings: list[str] | None = None


class ScannerGate(Protocol):
    """Минимальный интерфейс gRPC-клиента observer'а.

    Прод-реализация — обёртка над BrowserAgentClient.run_scan_cycle().
    Тест-реализация — заранее заготовленный список ScannedAdRow.
    """

    async def run_one_scan(self) -> ScanCycleOutput:
        """Делает один scan-цикл и возвращает все собранные строки + метаданные.

        Если сканер вернул ошибку — поднимает исключение (loop сам решит retry).
        """
        ...


# ====================== Scan_runs writers ======================


async def _begin_scan_run(engine: AsyncEngine) -> int:
    """INSERT в partitioned scan_runs → возвращаем монотонный id.

    Атомарный: scan_id = id за один INSERT через CTE с явным nextval.
    Никакого последующего UPDATE — если процесс крашится до RETURNING, sequence
    откатится вместе с транзакцией и осиротевшего scan_id не возникнет.
    """
    started_at = datetime.now(timezone.utc)
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    WITH next_id AS (SELECT nextval('scan_runs_id_seq') AS sid)
                    INSERT INTO scan_runs (id, scan_id, started_at)
                    SELECT sid, sid, :sa FROM next_id
                    RETURNING id
                    """
                ),
                {"sa": started_at},
            )
        ).first()
    return int(row[0])


async def _finish_scan_run(
    engine: AsyncEngine,
    *,
    scan_id: int,
    outcome: str,
    cycle_result: CycleResult | None,
    error_message: str | None = None,
    duration_ms: int = 0,
) -> None:
    """UPDATE финальным результатом цикла."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE scan_runs
                SET finished_at = NOW(),
                    outcome = :oc,
                    rows_total = :rt,
                    alerts_warning = :aw,
                    alerts_stop = :as,
                    error_message = :em,
                    duration_ms = :dm
                WHERE id = :id
                """
            ),
            {
                "id": scan_id,
                "oc": outcome,
                "rt": cycle_result.rows_total if cycle_result else 0,
                "aw": cycle_result.alerts_warning if cycle_result else 0,
                "as": cycle_result.alerts_stop if cycle_result else 0,
                "em": error_message,
                "dm": duration_ms,
            },
        )


# ====================== Redis heartbeat ======================


async def _publish_runtime_status(
    redis_client,
    *,
    status: str,
    active_phase: str | None = None,
    next_scan_at: datetime | None = None,
    last_successful_scan_at: datetime | None = None,
) -> None:
    """SET observer:runtime → JSON с TTL 60s. Frontend/health_watchdog читают этот ключ.

    Контракт:
        worker_status — детальный статус: "scanning" | "idle" | "dispatch" | "paused"
        status        — нормализованный для читателей: "running" | "paused"
            Маппинг: scanning/idle/dispatch → running, paused → paused

    Читатели используют read_observer_runtime() из core/observer/runtime.py.
    """
    if redis_client is None:
        return

    # Нормализованный статус для читателей (scanning/idle/dispatch → running)
    _RUNNING_DETAIL = {"scanning", "idle", "dispatch"}
    normalized_status = "running" if status in _RUNNING_DETAIL else status

    payload = {
        "worker_status": status,  # детальный (для отладки/granularity)
        "status": normalized_status,  # нормализованный (running|paused) для читателей
        "active_phase": active_phase,
        "next_scan_at": next_scan_at.isoformat() if next_scan_at else None,
        "last_successful_scan_at": (
            last_successful_scan_at.isoformat() if last_successful_scan_at else None
        ),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await redis_client.set("observer:runtime", json.dumps(payload), ex=60)
    except Exception:
        logger.exception("redis SET observer:runtime failed")


async def _publish_scan_finished(
    redis_client,
    *,
    scan_id: int,
    outcome: str,
    cycle_result: CycleResult | None,
) -> None:
    """PUBLISH fb_agent:scan:finished — trigger refetch на фронте."""
    if redis_client is None:
        return
    event = {
        "scan_id": scan_id,
        "outcome": outcome,
        "rows_total": cycle_result.rows_total if cycle_result else 0,
        "alerts_warning": cycle_result.alerts_warning if cycle_result else 0,
        "alerts_stop": cycle_result.alerts_stop if cycle_result else 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await redis_client.publish("fb_agent:scan:finished", json.dumps(event))
    except Exception:
        logger.exception("redis PUBLISH fb_agent:scan:finished failed")


# ====================== Heartbeat ======================


async def heartbeat_loop(redis_client, stop: asyncio.Event) -> None:
    """Периодически пишет worker:heartbeat:observer с TTL 60s.

    Параллельный таск — не блокирует main-loop сканирования.
    """
    if redis_client is None:
        return
    interval = HEARTBEAT_TTL_SECONDS / 2
    while not stop.is_set():
        try:
            await redis_client.set(HEARTBEAT_KEY, "alive", ex=HEARTBEAT_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            logger.exception("observer heartbeat: ошибка записи в Redis")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


# ====================== One cycle ======================


async def run_one_cycle(
    engine: AsyncEngine,
    *,
    gate: ScannerGate,
    redis_client=None,
    tg_client=None,
) -> dict:
    """Один полный цикл observer'а. Возвращает summary для логов/тестов.

    Не бросает исключения наверх — все ошибки логирует и записывает в scan_runs.outcome.

    Если tg_client передан — после process_scan_rows зовём dispatch_pending_alerts(scan_id):
    события записанные в этом scan'е улетают в TG чат с inline-кнопками.
    """
    config = await load_observer_config(engine)
    if config is None or not config["is_scanning_enabled"]:
        await _publish_runtime_status(redis_client, status="paused")
        return {"outcome": "paused", "scan_id": None}

    scan_id = await _begin_scan_run(engine)
    started_monotonic = time.monotonic()
    await _publish_runtime_status(redis_client, status="scanning", active_phase="scan")

    cycle_result: CycleResult | None = None
    outcome = "success"
    error_msg: str | None = None
    dispatched: dict | None = None

    try:
        scan_out = await gate.run_one_scan()

        if not scan_out.rows:
            outcome = "empty"
            error_msg = scan_out.empty_reason or "no rows"
        else:
            await _publish_runtime_status(redis_client, status="scanning", active_phase="parse")
            cycle_result = await process_scan_rows(engine, rows=scan_out.rows, scan_id=scan_id)

            # Доставка алертов в TG — если был хоть один emit
            if (
                tg_client is not None
                and cycle_result
                and (cycle_result.alerts_warning + cycle_result.alerts_stop > 0)
            ):
                from core.telegram.alert_dispatcher import dispatch_pending_alerts

                await _publish_runtime_status(
                    redis_client, status="scanning", active_phase="dispatch"
                )
                try:
                    dispatched = await dispatch_pending_alerts(
                        engine, client=tg_client, scan_id=scan_id
                    )
                except Exception:
                    logger.exception("alert dispatch failed — продолжаю")
                    dispatched = {"sent": 0, "errors": 1}
    except Exception as exc:
        logger.exception("scan cycle crashed: %s", exc)
        outcome = "error"
        error_msg = f"{type(exc).__name__}: {exc}"

    duration_ms = int((time.monotonic() - started_monotonic) * 1000)
    await _finish_scan_run(
        engine,
        scan_id=scan_id,
        outcome=outcome,
        cycle_result=cycle_result,
        error_message=error_msg,
        duration_ms=duration_ms,
    )

    await _publish_scan_finished(
        redis_client, scan_id=scan_id, outcome=outcome, cycle_result=cycle_result
    )
    await _publish_runtime_status(
        redis_client,
        status="idle",
        last_successful_scan_at=datetime.now(timezone.utc) if outcome == "success" else None,
    )

    return {
        "outcome": outcome,
        "scan_id": scan_id,
        "duration_ms": duration_ms,
        "rows_total": cycle_result.rows_total if cycle_result else 0,
        "alerts_warning": cycle_result.alerts_warning if cycle_result else 0,
        "alerts_stop": cycle_result.alerts_stop if cycle_result else 0,
        "tg_dispatched": dispatched,
        "error": error_msg,
    }


# ====================== Shared state для pubsub-сигналов ======================


@dataclass
class _ObserverState:
    """Разделяемое состояние между main_loop и pubsub-handler'ами."""

    force_scan_pending: bool = False  # выставляется триггером fb_agent:observer:trigger
    should_stop: bool = False  # выставляется сигналом restart


# ====================== Main loop ======================


async def main_loop(
    *,
    gate_factory: Callable[[], Awaitable[ScannerGate]] | None = None,
    redis_factory: Callable[[], Awaitable[object]] | None = None,
    tg_client_factory: Callable[[], Awaitable[object]] | None = None,
    should_continue: Callable[[], bool] = lambda: True,
) -> None:
    """Бесконечный цикл observer.

    Args:
        gate_factory: создаёт ScannerGate (default: BrowserAgentClient wrapper).
        redis_factory: создаёт redis.asyncio.Redis для heartbeat.
        tg_client_factory: создаёт TelegramBotClient для отправки алертов.
            Если None — алерты не отправляются (полезно в тестах).
        should_continue: для тестов — управляет выходом из цикла.
    """
    from core.control.pubsub_listener import RedisPubSubListener

    db_url = _get_database_url()
    engine = create_async_engine(db_url, echo=False)

    if gate_factory is None:
        gate_factory = _default_gate_factory
    if redis_factory is None:
        redis_factory = _default_redis_factory
    if tg_client_factory is None:
        # bind engine в default factory чтобы интерфейс остался Callable[[], Awaitable]
        async def _bound_tg_factory():
            return await _default_tg_client_factory(engine)

        tg_client_factory = _bound_tg_factory

    # Graceful shutdown по SIGTERM/SIGINT.
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except (NotImplementedError, RuntimeError):
            pass

    # Разделяемое состояние для pubsub-handler'ов.
    state = _ObserverState()

    gate: ScannerGate | None = None
    redis_client = None
    tg_client = None
    listener_task: asyncio.Task | None = None
    listener: RedisPubSubListener | None = None
    heartbeat_task: asyncio.Task | None = None

    try:
        redis_client = await redis_factory()
        tg_client = await tg_client_factory()
        logger.info("observer_worker запущен")

        # Запускаем heartbeat-таск если Redis доступен.
        if redis_client is not None:
            heartbeat_task = asyncio.create_task(heartbeat_loop(redis_client, shutdown_event))

        # Подписываемся на управляющие каналы если Redis доступен.
        if redis_client is not None:
            listener = RedisPubSubListener(
                redis_client,
                [CHANNEL_TRIGGER, CHANNEL_CABINET_DAY, CHANNEL_RESTART],
            )

            async def _on_trigger(_payload: dict) -> None:
                """Форс-скан: выставляем флаг, цикл пропустит sleep."""
                logger.info("observer: получен trigger scan-now")
                state.force_scan_pending = True

            async def _on_cabinet_day(_payload: dict) -> None:
                """Сигнал нового кабинетного дня — TODO: реализовать архивирование."""
                logger.info(
                    "observer: получен сигнал cabinet_day "
                    "(TODO: реализовать archive-логику при наличии cabinet_day метода)"
                )

            async def _on_restart(_payload: dict) -> None:
                """Graceful restart: выставляем should_stop + shutdown_event."""
                logger.info("observer: получен сигнал restart по каналу %s", CHANNEL_RESTART)
                state.should_stop = True
                shutdown_event.set()

            listener.register(CHANNEL_TRIGGER, _on_trigger)
            listener.register(CHANNEL_CABINET_DAY, _on_cabinet_day)
            listener.register(CHANNEL_RESTART, _on_restart)
            listener_task = asyncio.create_task(listener.run_forever())

        while should_continue() and not shutdown_event.is_set() and not state.should_stop:
            if gate is None:
                try:
                    gate = await gate_factory()
                except Exception:
                    logger.exception("Не смог создать gate — sleep 10s")
                    await asyncio.sleep(10.0)
                    continue

            try:
                summary = await run_one_cycle(
                    engine,
                    gate=gate,
                    redis_client=redis_client,
                    tg_client=tg_client,
                )
                logger.info("cycle done: %s", summary)
            except Exception:
                logger.exception("run_one_cycle crashed — пересоздаю gate")
                gate = None
                await asyncio.sleep(10.0)
                continue

            # Если выставлен форс-скан — немедленно делаем следующий цикл без sleep.
            if state.force_scan_pending:
                logger.info("observer: force_scan_pending — пропускаю sleep, запускаю сразу")
                state.force_scan_pending = False
                continue

            # Интервал до следующего цикла
            config = await load_observer_config(engine)
            interval = (config or {}).get("interval_seconds", 90)
            jitter = (config or {}).get("jitter_seconds", 15)
            sleep_for = float(interval) + random.uniform(0, float(jitter))

            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=sleep_for)
            except asyncio.TimeoutError:
                pass

            # Если trigger пришёл во время sleep — не ждём следующего цикла.
            if state.force_scan_pending:
                logger.info("observer: force_scan_pending (после sleep) — не ждём")
                state.force_scan_pending = False
    finally:
        logger.info("observer_worker завершён")

        # Останавливаем heartbeat-таск.
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

        # Останавливаем pubsub-listener.
        if listener is not None:
            try:
                await listener.stop()
            except Exception:
                pass
        if listener_task is not None:
            listener_task.cancel()
            try:
                await listener_task
            except asyncio.CancelledError:
                pass

        if redis_client is not None:
            try:
                await redis_client.aclose()
            except Exception:
                pass
        await engine.dispose()


# ====================== Default factories (прод-реализация) ======================


async def _default_gate_factory() -> ScannerGate:
    """Прод-реализация: оборачивает BrowserAgentClient в ScannerGate-протокол."""
    from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig
    from clients.python_grpc.client import ScanResult as GrpcScanResult
    from core.config import get_settings

    s = get_settings()
    client = BrowserAgentClient(
        BrowserAgentConfig(
            vision_x_token=s.vision_x_token,
            vision_api_url=s.vision_api_url,
            vision_profile_id=s.vision_profile_id,
        )
    )
    await client.start()
    # run_scan_cycle сам поднимет browser-сессию (ensure_browser_session внутри).

    class _BrowserAgentScannerGate:
        async def run_one_scan(self) -> ScanCycleOutput:
            final_result: GrpcScanResult | None = None
            async for event in client.run_scan_cycle():
                # ScanProgress нам пока не нужен — слушаем только финальный ScanResult
                if isinstance(event, GrpcScanResult):
                    final_result = event
            if final_result is None:
                return ScanCycleOutput(rows=[], empty_reason="no final result")
            return ScanCycleOutput(
                rows=final_result.rows,
                total_passes=final_result.total_passes,
                duration_seconds=final_result.duration_seconds,
                empty_reason=final_result.empty_reason,
                warnings=list(final_result.warnings),
            )

    return _BrowserAgentScannerGate()


async def _default_redis_factory():
    """Прод-реализация: redis.asyncio.Redis к docker-compose:6380."""
    try:
        import redis.asyncio as redis_async  # type: ignore
    except ImportError:
        logger.warning("redis package не установлен — heartbeat отключён")
        return None

    redis_url = os.environ.get("REDIS_URL", "")
    if redis_url:
        return redis_async.from_url(redis_url, decode_responses=True)

    host = os.environ.get("REDIS_HOST", "127.0.0.1")
    port = int(os.environ.get("REDIS_PORT", "6380"))
    return redis_async.Redis(host=host, port=port, decode_responses=True)


async def _default_tg_client_factory(engine):
    """Прод-реализация: TelegramBotClient из telegram_config (если есть)."""
    import httpx

    from core.telegram.client import TelegramBotClient
    from core.telegram.service import load_telegram_config

    cfg = await load_telegram_config(engine)
    if cfg is None or not cfg.bot_token:
        logger.warning("telegram_config пустой — алерты в TG отключены")
        return None
    return TelegramBotClient(bot_token=cfg.bot_token, http_client=httpx.AsyncClient(timeout=30.0))

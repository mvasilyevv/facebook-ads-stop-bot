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
    """INSERT в partitioned scan_runs → возвращаем монотонный id."""
    started_at = datetime.now(timezone.utc)
    async with engine.begin() as conn:
        # scan_id = id (BigSerial). Используем сам id как scan_id для простоты.
        # Если позже потребуется отдельный counter — добавим Redis INCR.
        row = (
            await conn.execute(
                text(
                    """
                    INSERT INTO scan_runs (scan_id, started_at)
                    VALUES (nextval('scan_runs_id_seq'), :sa)
                    RETURNING id
                    """
                ),
                {"sa": started_at},
            )
        ).first()
        scan_id = int(row[0])

        # scan_id и id должны совпадать — обновим scan_id чтобы он реально стал id'шником
        await conn.execute(
            text("UPDATE scan_runs SET scan_id = id WHERE id = :i AND started_at = :sa"),
            {"i": scan_id, "sa": started_at},
        )
    return scan_id


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
    """SET observer:runtime → JSON с TTL 60s. Frontend/health_watchdog читают этот ключ."""
    if redis_client is None:
        return
    payload = {
        "worker_status": status,
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

    # Graceful shutdown
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except (NotImplementedError, RuntimeError):
            pass

    gate: ScannerGate | None = None
    redis_client = None
    tg_client = None

    try:
        redis_client = await redis_factory()
        tg_client = await tg_client_factory()
        logger.info("observer_worker запущен")

        while should_continue() and not shutdown_event.is_set():
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

            # Интервал до следующего цикла
            config = await load_observer_config(engine)
            interval = (config or {}).get("interval_seconds", 90)
            jitter = (config or {}).get("jitter_seconds", 15)
            sleep_for = float(interval) + random.uniform(0, float(jitter))

            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=sleep_for)
            except asyncio.TimeoutError:
                pass
    finally:
        logger.info("observer_worker завершён")
        if redis_client is not None:
            try:
                await redis_client.aclose()
            except Exception:
                pass
        await engine.dispose()


# ====================== Default factories (прод-реализация) ======================


async def _default_gate_factory() -> ScannerGate:
    """Прод-реализация: оборачивает BrowserAgentClient в ScannerGate-протокол."""
    from clients.python_grpc.client import BrowserAgentClient
    from clients.python_grpc.client import ScanResult as GrpcScanResult

    client = BrowserAgentClient()
    await client.connect()

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
    import os

    try:
        import redis.asyncio as redis_async  # type: ignore
    except ImportError:
        logger.warning("redis package не установлен — heartbeat отключён")
        return None

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

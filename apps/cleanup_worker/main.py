# -*- coding: utf-8 -*-
"""Entrypoint cleanup_worker — раз в сутки в 04:00 UTC."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import create_async_engine

from apps.cleanup_worker.storage import (
    collect_disk_space,
    disk_thresholds_from_env,
    publish_cleanup_freshness,
    publish_cleanup_run_health,
    publish_disk_check_unavailable,
    publish_disk_health,
)
from apps.cleanup_worker.worker import create_next_partition_if_missing, run_once
from core.db import WORKER_ENGINE_KWARGS
from core.worker_liveness import record_worker_heartbeat
from core.worker_metrics import mark_worker_db_poll_success, mark_worker_heartbeat

logger = logging.getLogger("cleanup_worker")

WORKER_NAME = "cleanup"
_METRICS_INTERVAL_SECONDS = 15.0
_STORAGE_CHECK_INTERVAL_SECONDS = 15 * 60.0

# Час прогона (UTC), default 4:00
_RUN_HOUR_UTC = int(os.environ.get("CLEANUP_WORKER_RUN_HOUR_UTC", "4"))


async def metrics_loop(stop: asyncio.Event, engine) -> None:
    """Refresh heartbeat plus disk/run health between daily cleanup runs."""
    next_storage_check = 0.0
    while not stop.is_set():
        mark_worker_heartbeat(WORKER_NAME)
        await record_worker_heartbeat(engine, WORKER_NAME)
        loop_time = asyncio.get_running_loop().time()
        if loop_time >= next_storage_check:
            try:
                await publish_cleanup_freshness(
                    engine,
                    now=datetime.now(timezone.utc),
                )
                mark_worker_db_poll_success(WORKER_NAME)
                await record_worker_heartbeat(engine, WORKER_NAME, poll_success=True)
            except Exception as exc:
                logger.exception("cleanup freshness check failed: %s", exc)
            try:
                min_free_bytes, min_free_ratio = disk_thresholds_from_env()
                await publish_disk_health(
                    engine,
                    disk=collect_disk_space(),
                    min_free_bytes=min_free_bytes,
                    min_free_ratio=min_free_ratio,
                )
            except Exception as exc:
                logger.exception("database disk check failed: %s", exc)
                accepted = await publish_disk_check_unavailable(engine)
                if not accepted:
                    logger.warning("disk check incident was not accepted by durable plane")
            next_storage_check = loop_time + _STORAGE_CHECK_INTERVAL_SECONDS
        try:
            await asyncio.wait_for(stop.wait(), timeout=_METRICS_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass


async def _run_cleanup(engine):
    """Run cleanup and emit an immediate durable warning on an unhandled crash."""
    try:
        return await run_once(engine)
    except Exception:
        accepted = await publish_cleanup_run_health(
            engine,
            success=False,
            error_count=1,
        )
        if not accepted:
            logger.warning("cleanup crash incident was not accepted by durable plane")
        raise


async def _initialize_partition_storage(
    engine,
    *,
    run_cleanup_on_start: bool,
) -> None:
    """Create current/next partitions before any retention work or sleep.

    The Alembic baseline deliberately owns only date-independent DEFAULT
    partitions.  Every cleanup process therefore materializes the current and
    next month as its first database operation; the DEFAULT partitions remain
    the fail-safe route if the process is temporarily unavailable.
    """

    created = await create_next_partition_if_missing(engine, fail_on_error=True)
    logger.info("Startup partition preparation complete: %s", created)
    if run_cleanup_on_start:
        logger.info("CLEANUP_RUN_ON_START=true → запуск сразу")
        await _run_cleanup(engine)


def _seconds_until_next_run(now: datetime) -> float:
    """Сколько секунд до следующего запуска (04:00 UTC)."""
    target = now.replace(hour=_RUN_HOUR_UTC, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def main_loop(database_url: str) -> None:
    engine = create_async_engine(database_url, **WORKER_ENGINE_KWARGS)
    stop_event = asyncio.Event()

    def _handle_sigterm() -> None:
        logger.info("Получен сигнал остановки.")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_sigterm)
        except (NotImplementedError, ValueError):
            pass

    metrics_task = asyncio.create_task(metrics_loop(stop_event, engine))

    try:
        await _initialize_partition_storage(
            engine,
            run_cleanup_on_start=(
                os.environ.get("CLEANUP_RUN_ON_START", "false").lower() == "true"
            ),
        )

        while not stop_event.is_set():
            now = datetime.now(timezone.utc)
            sleep_s = _seconds_until_next_run(now)
            logger.info(
                "Следующий прогон через %.0f секунд (~%s часов)",
                sleep_s,
                int(sleep_s // 3600),
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=sleep_s)
                break  # stop_event сработал
            except asyncio.TimeoutError:
                pass

            if stop_event.is_set():
                break

            try:
                await _run_cleanup(engine)
            except Exception as exc:
                logger.exception("run_once упал: %s", exc)
                # Не падаем — спим до следующего запланированного прогона
    finally:
        stop_event.set()
        metrics_task.cancel()
        try:
            await metrics_task
        except asyncio.CancelledError:
            pass
        await engine.dispose()
        logger.info("cleanup_worker остановлен.")


def _get_database_url() -> str:
    """Берёт URL из core.config.get_settings()."""
    from core.config import get_settings

    return get_settings().database_url


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    db_url = _get_database_url()
    asyncio.run(main_loop(db_url))

# -*- coding: utf-8 -*-
"""Entrypoint reconciler_worker — каждые 30 секунд."""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from sqlalchemy.ext.asyncio import create_async_engine

from apps.reconciler_worker.worker import run_once
from core.db import WORKER_ENGINE_KWARGS
from core.tasks.queue import refresh_task_queue_metrics
from core.worker_liveness import record_worker_heartbeat
from core.worker_metrics import mark_worker_heartbeat, start_worker_metrics_server

logger = logging.getLogger("reconciler_worker")

WORKER_NAME = "reconciler"
_METRICS_INTERVAL_SECONDS = 15.0

_INTERVAL_SEC = int(os.environ.get("RECONCILER_INTERVAL_SEC", "30"))


async def metrics_loop(stop: asyncio.Event, engine=None) -> None:
    """Refresh process and durable queue Prometheus metrics."""
    while not stop.is_set():
        mark_worker_heartbeat(WORKER_NAME)
        if engine is not None:
            await record_worker_heartbeat(engine, WORKER_NAME)
            try:
                await refresh_task_queue_metrics(engine)
            except Exception:  # noqa: BLE001
                logger.exception("reconciler metrics: failed to refresh queue metrics")
        try:
            await asyncio.wait_for(stop.wait(), timeout=_METRICS_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass


async def main_loop(database_url: str) -> None:
    engine = create_async_engine(database_url, **WORKER_ENGINE_KWARGS)
    start_worker_metrics_server(WORKER_NAME)
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

    metrics_task = asyncio.create_task(metrics_loop(stop_event, engine=engine))

    try:
        while not stop_event.is_set():
            try:
                await run_once(engine)
            except Exception as exc:
                logger.exception("run_once упал: %s", exc)
            # run_once уже поймал и залогировал свою ошибку — дойти досюда
            # значит рабочий цикл жив и реально трогает БД (issue #176).
            await record_worker_heartbeat(engine, WORKER_NAME, poll_success=True)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_INTERVAL_SEC)
                break
            except asyncio.TimeoutError:
                pass
    finally:
        stop_event.set()
        metrics_task.cancel()
        try:
            await metrics_task
        except asyncio.CancelledError:
            pass
        await engine.dispose()
        logger.info("reconciler_worker остановлен.")


def _get_database_url() -> str:
    from core.config import get_settings

    return get_settings().database_url


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    asyncio.run(main_loop(_get_database_url()))

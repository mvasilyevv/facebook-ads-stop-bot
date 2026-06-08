# -*- coding: utf-8 -*-
"""Entrypoint reconciler_worker — каждые 30 секунд."""

from __future__ import annotations

import asyncio
import logging
import os
import signal

import redis.asyncio as redis_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from apps.reconciler_worker.worker import run_once
from core.db import WORKER_ENGINE_KWARGS

logger = logging.getLogger("reconciler_worker")

# Heartbeat — имя ДОЛЖНО совпадать с EXPECTED_WORKERS в health_watchdog.
WORKER_NAME = "reconciler"
HEARTBEAT_KEY = f"worker:heartbeat:{WORKER_NAME}"
HEARTBEAT_TTL_SECONDS = 60

_INTERVAL_SEC = int(os.environ.get("RECONCILER_INTERVAL_SEC", "30"))


def _get_redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6380/0")


async def heartbeat_loop(redis_client, stop: asyncio.Event) -> None:
    """Периодически пишет worker:heartbeat:reconciler с TTL 60s.

    Параллельный таск — не блокирует основной цикл reconciliation.
    """
    interval = HEARTBEAT_TTL_SECONDS / 2
    while not stop.is_set():
        try:
            await redis_client.set(HEARTBEAT_KEY, "alive", ex=HEARTBEAT_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            logger.exception("reconciler heartbeat: ошибка записи в Redis")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


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

    # Запускаем heartbeat параллельно с основным циклом.
    hb_redis: redis_asyncio.Redis | None = None
    hb_task: asyncio.Task | None = None
    try:
        hb_redis = redis_asyncio.from_url(_get_redis_url(), decode_responses=True)
        hb_task = asyncio.create_task(heartbeat_loop(hb_redis, stop_event))
    except Exception:
        logger.warning("reconciler_worker: не удалось запустить heartbeat")

    try:
        while not stop_event.is_set():
            try:
                await run_once(engine)
            except Exception as exc:
                logger.exception("run_once упал: %s", exc)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_INTERVAL_SEC)
                break
            except asyncio.TimeoutError:
                pass
    finally:
        # Останавливаем heartbeat-таск.
        stop_event.set()
        if hb_task is not None:
            hb_task.cancel()
            try:
                await hb_task
            except asyncio.CancelledError:
                pass
        if hb_redis is not None:
            try:
                await hb_redis.aclose()
            except Exception:
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

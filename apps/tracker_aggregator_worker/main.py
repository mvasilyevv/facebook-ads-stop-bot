# -*- coding: utf-8 -*-
"""Entrypoint tracker_aggregator_worker — прогон раз в N минут."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import timedelta

import redis.asyncio as redis_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from apps.tracker_aggregator_worker.worker import DEFAULT_LOOKBACK, run_once

logger = logging.getLogger("tracker_aggregator_worker")

# Heartbeat — имя ДОЛЖНО совпадать с EXPECTED_WORKERS в health_watchdog (если включат).
WORKER_NAME = "tracker_aggregator"
HEARTBEAT_KEY = f"worker:heartbeat:{WORKER_NAME}"
HEARTBEAT_TTL_SECONDS = 60

# Интервал между прогонами (сек), default 5 минут.
_INTERVAL_SECONDS = int(os.environ.get("TRACKER_AGGREGATOR_INTERVAL_SECONDS", "300"))
# Окно lookback (сек), default 2 часа (см. worker.DEFAULT_LOOKBACK).
_LOOKBACK_SECONDS = int(
    os.environ.get(
        "TRACKER_AGGREGATOR_LOOKBACK_SECONDS", str(int(DEFAULT_LOOKBACK.total_seconds()))
    )
)


def _get_redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6380/0")


async def heartbeat_loop(redis_client, stop: asyncio.Event) -> None:
    """Пишет worker:heartbeat:tracker_aggregator с TTL 60s пока воркер жив."""
    interval = HEARTBEAT_TTL_SECONDS / 2
    while not stop.is_set():
        try:
            await redis_client.set(HEARTBEAT_KEY, "alive", ex=HEARTBEAT_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            logger.exception("tracker_aggregator heartbeat: ошибка записи в Redis")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def main_loop(database_url: str) -> None:
    engine = create_async_engine(database_url, echo=False)
    stop_event = asyncio.Event()
    lookback = timedelta(seconds=_LOOKBACK_SECONDS)

    def _handle_sigterm() -> None:
        logger.info("Получен сигнал остановки.")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_sigterm)
        except (NotImplementedError, ValueError):
            pass

    hb_redis: redis_asyncio.Redis | None = None
    hb_task: asyncio.Task | None = None
    try:
        hb_redis = redis_asyncio.from_url(_get_redis_url(), decode_responses=True)
        hb_task = asyncio.create_task(heartbeat_loop(hb_redis, stop_event))
    except Exception:
        logger.warning("tracker_aggregator_worker: не удалось запустить heartbeat")

    try:
        while not stop_event.is_set():
            try:
                await run_once(engine, lookback=lookback)
            except Exception as exc:
                logger.exception("tracker_aggregator run_once упал: %s", exc)
                # Не падаем — спим до следующего прогона

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_INTERVAL_SECONDS)
                break  # stop_event сработал
            except asyncio.TimeoutError:
                pass
    finally:
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
            except Exception:  # noqa: BLE001
                pass
        await engine.dispose()
        logger.info("tracker_aggregator_worker остановлен.")


def _get_database_url() -> str:
    from core.config import get_settings

    return get_settings().database_url


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    asyncio.run(main_loop(_get_database_url()))

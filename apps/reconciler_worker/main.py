# -*- coding: utf-8 -*-
"""Entrypoint reconciler_worker — каждые 30 секунд."""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from sqlalchemy.ext.asyncio import create_async_engine

from apps.reconciler_worker.worker import run_once

logger = logging.getLogger("reconciler_worker")

_INTERVAL_SEC = int(os.environ.get("RECONCILER_INTERVAL_SEC", "30"))


async def main_loop(database_url: str) -> None:
    engine = create_async_engine(database_url, echo=False)
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

# -*- coding: utf-8 -*-
"""Точка входа: запускает recommendation worker для выключенных объявлений."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from apps.enable_recommendation_worker.main import recommendation_worker_loop
from core.config import get_settings
from core.sentry import setup_sentry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Запускает цикл recommendation worker и корректно останавливает его по сигналу."""
    _s = get_settings()
    setup_sentry(dsn=_s.sentry_dsn, environment=_s.sentry_environment)
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _handle_signal() -> None:
        logger.info("Получен сигнал остановки recommendation worker")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    await recommendation_worker_loop(shutdown_event=shutdown_event)


if __name__ == "__main__":
    asyncio.run(main())

from __future__ import annotations

import asyncio
import logging

from apps.worker.scheduler import SchedulerService
from core.logging.setup import configure_logging


async def run_worker() -> None:
    configure_logging()
    logger = logging.getLogger(__name__)
    scheduler = SchedulerService()
    logger.info("Фоновый воркер запущен")
    await scheduler.start()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()

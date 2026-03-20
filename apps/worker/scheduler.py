from __future__ import annotations

import asyncio
import logging


class SchedulerService:
    """Простейший каркас планировщика фоновых задач."""

    async def start(self) -> None:
        logger = logging.getLogger(__name__)
        logger.info("Планировщик готов к публикации заданий")
        await asyncio.Event().wait()

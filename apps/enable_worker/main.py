# -*- coding: utf-8 -*-
"""Enable worker main loop."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import create_async_engine

from apps.disable_worker.main import _make_browser_gate
from apps.telegram_poller.main import _get_database_url
from core.tasks.toggle_executor import run_toggle_loop

logger = logging.getLogger(__name__)


async def main_loop() -> None:
    db_url = _get_database_url()
    engine = create_async_engine(db_url, echo=False)
    try:
        logger.info("enable_worker запущен")
        await run_toggle_loop(
            engine,
            task_type="enable",
            gate_factory=_make_browser_gate,
        )
    finally:
        await engine.dispose()
        logger.info("enable_worker завершён")

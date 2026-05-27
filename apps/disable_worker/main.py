# -*- coding: utf-8 -*-
"""Disable worker v2 main loop."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import create_async_engine

from apps.telegram_poller.main import _get_database_url
from core.tasks.toggle_executor import run_toggle_loop

logger = logging.getLogger(__name__)


async def _make_browser_gate():
    """Создаёт реальный BrowserAgentClient — отдельная функция для удобства моков в тестах."""
    from clients.python_grpc.client import BrowserAgentClient

    # Минимальный конструктор — настройки берутся из env через config внутри клиента
    client = BrowserAgentClient()
    await client.connect()
    return client


async def main_loop() -> None:
    db_url = _get_database_url()
    engine = create_async_engine(db_url, echo=False)
    try:
        logger.info("disable_worker_v2 запущен")
        await run_toggle_loop(
            engine,
            task_type="disable",
            gate_factory=_make_browser_gate,
        )
    finally:
        await engine.dispose()
        logger.info("disable_worker_v2 завершён")

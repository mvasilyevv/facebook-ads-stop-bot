# -*- coding: utf-8 -*-
"""Disable worker main loop."""

from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy.ext.asyncio import create_async_engine

from apps.telegram_poller.main import _get_database_url
from core.control.pubsub_listener import RedisPubSubListener
from core.tasks.toggle_executor import run_toggle_loop

logger = logging.getLogger(__name__)

# Канал управляющего сигнала для этого воркера.
CHANNEL_RESTART = "fb_agent:worker:restart:disable_worker"


async def _make_browser_gate():
    """Создаёт реальный BrowserAgentClient — отдельная функция для удобства моков в тестах."""
    from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig
    from core.config import get_settings

    s = get_settings()
    client = BrowserAgentClient(
        BrowserAgentConfig(
            vision_x_token=s.vision_x_token,
            vision_api_url=s.vision_api_url,
            vision_profile_id=s.vision_profile_id,
        )
    )
    await client.start()
    return client


def _get_redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6380/0")


async def main_loop(
    *,
    redis_factory=None,
    should_continue=lambda: True,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Основной цикл disable_worker с поддержкой Redis-сигнала restart.

    Args:
        redis_factory: фабрика Redis-клиента (None — прод-реализация из env).
        should_continue: хук для тестов — управляет числом итераций toggle-loop.
        stop_event: внешний Event для graceful-stop (в тестах можно передать напрямую).
    """
    db_url = _get_database_url()
    engine = create_async_engine(db_url, echo=False)

    # Создаём stop_event если не передан снаружи.
    if stop_event is None:
        stop_event = asyncio.Event()

    # Инициализируем Redis-клиент.
    redis_client = None
    if redis_factory is not None:
        redis_client = await redis_factory()
    else:
        try:
            import redis.asyncio as redis_async  # type: ignore

            redis_client = redis_async.from_url(_get_redis_url(), decode_responses=True)
        except Exception:
            logger.warning("disable_worker: Redis недоступен — pubsub-listener отключён")

    listener: RedisPubSubListener | None = None
    listener_task: asyncio.Task | None = None

    try:
        logger.info("disable_worker запущен")

        # Запускаем pubsub-listener если есть Redis.
        if redis_client is not None:
            listener = RedisPubSubListener(redis_client, [CHANNEL_RESTART])

            async def _on_restart(_payload: dict) -> None:
                """Получен сигнал restart — выставляем stop_event."""
                logger.info("disable_worker: получен сигнал restart по каналу %s", CHANNEL_RESTART)
                stop_event.set()

            listener.register(CHANNEL_RESTART, _on_restart)
            listener_task = asyncio.create_task(listener.run_forever())

        await run_toggle_loop(
            engine,
            task_type="disable",
            gate_factory=_make_browser_gate,
            should_continue=should_continue,
            stop_event=stop_event,
            redis_client=redis_client,  # для worker:heartbeat:disable
        )
    finally:
        # Останавливаем listener.
        if listener is not None:
            try:
                await listener.stop()
            except Exception:
                pass
        if listener_task is not None:
            listener_task.cancel()
            try:
                await listener_task
            except asyncio.CancelledError:
                pass

        if redis_client is not None:
            try:
                await redis_client.aclose()
            except Exception:
                pass

        await engine.dispose()
        logger.info("disable_worker завершён")

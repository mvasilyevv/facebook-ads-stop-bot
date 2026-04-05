# -*- coding: utf-8 -*-
"""Базовый воркер для обработки задач из PostgresTaskQueue."""

from __future__ import annotations

import asyncio
import logging
from typing import Generic, TypeVar

from core.task_queue.postgres_queue import PostgresTaskQueue

logger = logging.getLogger(__name__)

T = TypeVar("T")


class BaseTaskWorker(Generic[T]):
    """Базовый воркер: claim → execute → mark результат.

    Подклассы реализуют ``execute_task`` с конкретной бизнес-логикой.
    Не содержит Playwright/browser-логику — это ответственность подклассов.

    Args:
        queue: Экземпляр PostgresTaskQueue для захвата и маркировки задач.
        poll_interval: Интервал поллинга очереди в секундах.
        worker_name: Имя воркера для логирования.
    """

    def __init__(
        self,
        queue: PostgresTaskQueue[T],
        *,
        poll_interval: int = 5,
        worker_name: str = "BaseTaskWorker",
    ) -> None:
        self._queue = queue
        self._poll_interval = poll_interval
        self._worker_name = worker_name

    @property
    def queue(self) -> PostgresTaskQueue[T]:
        """Доступ к очереди задач для подклассов."""
        return self._queue

    async def execute_task(self, task: T) -> tuple[bool, str]:
        """Выполняет конкретную задачу. Реализуется в подклассах.

        Returns:
            Кортеж (success, message).
        """
        raise NotImplementedError

    async def on_task_succeeded(self, task: T, message: str) -> None:
        """Хук после успешного выполнения задачи. Переопределяется при необходимости."""

    async def on_task_retrying(self, task: T, message: str) -> None:
        """Хук после постановки задачи на retry. Переопределяется при необходимости."""

    async def on_task_failed(self, task: T, message: str) -> None:
        """Хук после окончательного провала задачи. Переопределяется при необходимости."""

    async def _wait_for_next_poll(
        self,
        shutdown_event: asyncio.Event | None,
    ) -> bool:
        """Ждёт следующего цикла поллинга. Возвращает True если нужно выйти."""
        if shutdown_event:
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(),
                    timeout=self._poll_interval,
                )
                return True
            except asyncio.TimeoutError:
                return False
        await asyncio.sleep(self._poll_interval)
        return False

    async def _process_result(
        self,
        session: object,
        task: T,
        *,
        success: bool,
        message: str,
    ) -> None:
        """Маркирует задачу по итогу выполнения и вызывает хуки."""
        if success:
            await self._queue.mark_succeeded(session, task)  # type: ignore[arg-type]
            await self.on_task_succeeded(task, message)
            return

        if self._queue.is_exhausted(task):
            await self._queue.mark_failed(session, task, message)  # type: ignore[arg-type]
            await self.on_task_failed(task, message)
        else:
            await self._queue.mark_retrying(session, task, message)  # type: ignore[arg-type]
            await self.on_task_retrying(task, message)

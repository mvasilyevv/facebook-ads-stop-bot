# -*- coding: utf-8 -*-
"""Универсальная очередь задач на базе PostgreSQL с SELECT FOR UPDATE SKIP LOCKED."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, QueryableAttribute, selectinload

from core.worker_utils import calculate_retry_delay

logger = logging.getLogger(__name__)


@runtime_checkable
class TaskStatusEnum(Protocol):
    """Протокол для enum статусов задач (PENDING, RUNNING, RETRYING и т.д.)."""

    PENDING: str
    RUNNING: str
    RETRYING: str
    SUCCEEDED: str
    CANCELLED: str
    FAILED: str


T = TypeVar("T")


class PostgresTaskQueue(Generic[T]):
    """Очередь задач с атомарным захватом через SELECT FOR UPDATE SKIP LOCKED.

    Args:
        model_class: SQLAlchemy-модель задачи (DisableTask, EnableTask и т.д.).
        status_enum: StrEnum статусов задачи.
        eager_loads: Список relationship-атрибутов для selectinload при claim.
        order_column: Колонка для сортировки очереди (по умолчанию created_at).
    """

    def __init__(
        self,
        model_class: type[T],
        status_enum: type[StrEnum],
        *,
        eager_loads: list[QueryableAttribute[Any]] | None = None,
        order_column: InstrumentedAttribute[Any] | None = None,
    ) -> None:
        self._model = model_class
        self._status = status_enum
        self._eager_loads = eager_loads or []
        self._order_column = order_column

    def _base_query(self) -> Any:
        """Формирует базовый SELECT для захвата задач из очереди."""
        now = datetime.now(UTC)
        status_col = self._model.status  # type: ignore[attr-defined]
        retry_col = self._model.next_retry_at  # type: ignore[attr-defined]

        query = (
            select(self._model)
            .where(
                (status_col == self._status.PENDING)
                | ((status_col == self._status.RETRYING) & (retry_col <= now))
            )
            .with_for_update(skip_locked=True)
        )

        for rel in self._eager_loads:
            query = query.options(selectinload(rel))

        order = self._order_column
        if order is None:
            order = self._model.created_at  # type: ignore[attr-defined]
        return query.order_by(order.asc())

    async def claim_next(self, session: AsyncSession) -> T | None:
        """Захватывает одну задачу из очереди атомарно."""
        result = await session.execute(self._base_query().limit(1))
        task = result.scalar_one_or_none()
        if task is None:
            return None

        task.status = self._status.RUNNING  # type: ignore[attr-defined]
        task.attempt_count += 1  # type: ignore[attr-defined]
        return task

    async def claim_batch(
        self,
        session: AsyncSession,
        limit: int,
    ) -> list[T]:
        """Захватывает пачку задач из очереди атомарно."""
        result = await session.execute(self._base_query().limit(limit))
        tasks = list(result.scalars())
        if not tasks:
            return []

        for task in tasks:
            task.status = self._status.RUNNING  # type: ignore[attr-defined]
            task.attempt_count += 1  # type: ignore[attr-defined]
        return tasks

    async def mark_succeeded(self, session: AsyncSession, task: T) -> None:
        """Помечает задачу как успешно выполненную."""
        task.status = self._status.SUCCEEDED  # type: ignore[attr-defined]
        task.completed_at = datetime.now(UTC)  # type: ignore[attr-defined]
        task.next_retry_at = None  # type: ignore[attr-defined]
        task.last_error = None  # type: ignore[attr-defined]

    async def mark_retrying(
        self,
        session: AsyncSession,
        task: T,
        error: str,
    ) -> None:
        """Помечает задачу для повторной попытки с exponential backoff."""
        attempt = task.attempt_count  # type: ignore[attr-defined]
        delay = calculate_retry_delay(attempt)
        next_retry = datetime.now(UTC) + timedelta(seconds=delay)

        task.status = self._status.RETRYING  # type: ignore[attr-defined]
        task.last_error = error[:500]  # type: ignore[attr-defined]
        task.next_retry_at = next_retry  # type: ignore[attr-defined]

    async def mark_failed(
        self,
        session: AsyncSession,
        task: T,
        error: str,
    ) -> None:
        """Помечает задачу как окончательно проваленную."""
        task.status = self._status.FAILED  # type: ignore[attr-defined]
        task.last_error = error[:500]  # type: ignore[attr-defined]
        task.completed_at = datetime.now(UTC)  # type: ignore[attr-defined]
        task.next_retry_at = None  # type: ignore[attr-defined]

    def is_exhausted(self, task: T) -> bool:
        """Проверяет, исчерпаны ли все попытки у задачи."""
        return (
            task.attempt_count >= task.max_attempts  # type: ignore[attr-defined]
        )

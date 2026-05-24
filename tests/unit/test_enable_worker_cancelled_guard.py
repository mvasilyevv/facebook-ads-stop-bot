# -*- coding: utf-8 -*-
"""Гарантирует, что mark_succeeded/mark_retrying/mark_failed в enable_worker
не перетирают статус CANCELLED, выставленный позже через reconcile/API/observer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.domain import EnableTaskStatus


def _build_session_with_task(task) -> AsyncMock:
    """Создаёт мок AsyncSession, возвращающий заранее заданный task для select(EnableTask)."""
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    session.commit = AsyncMock()

    result_obj = MagicMock()
    result_obj.scalar_one_or_none.return_value = task
    session.execute = AsyncMock(return_value=result_obj)
    # На случай, если потребуется и snapshot — возвращаем None (для guard-тестов snapshot не нужен)
    session.scalar = AsyncMock(return_value=None)
    return session


def _patch_session_factory(monkeypatch, session: AsyncMock) -> None:
    """Подменяет get_session_factory в run_enable_worker, чтобы тесты работали без БД."""
    import run_enable_worker

    factory = MagicMock(return_value=session)
    monkeypatch.setattr(run_enable_worker, "get_session_factory", lambda: factory)


# enable_worker.mark_succeeded не перезаписывает CANCELLED-задачу
@pytest.mark.asyncio
async def test_mark_succeeded_skips_cancelled_task(monkeypatch):
    import run_enable_worker

    task = SimpleNamespace(
        id="enable-task-1",
        ad_id="ad-uuid-1",
        status=EnableTaskStatus.CANCELLED,
        completed_at=datetime.now(UTC),
        last_error="Отменена reconcile",
        next_retry_at=None,
    )
    session = _build_session_with_task(task)
    _patch_session_factory(monkeypatch, session)

    # Запоминаем поля до вызова — они не должны измениться.
    before_status = task.status
    before_error = task.last_error

    await run_enable_worker.mark_succeeded("enable-task-1")

    assert task.status == before_status == EnableTaskStatus.CANCELLED
    assert task.last_error == before_error
    # Поскольку guard сработал — commit не должен быть вызван.
    session.commit.assert_not_called()


# enable_worker.mark_retrying не перезаписывает CANCELLED-задачу
@pytest.mark.asyncio
async def test_mark_retrying_skips_cancelled_task(monkeypatch):
    import run_enable_worker

    task = SimpleNamespace(
        id="enable-task-2",
        ad_id="ad-uuid-2",
        status=EnableTaskStatus.CANCELLED,
        completed_at=datetime.now(UTC),
        last_error="Отменена reconcile",
        next_retry_at=None,
    )
    session = _build_session_with_task(task)
    _patch_session_factory(monkeypatch, session)

    await run_enable_worker.mark_retrying(
        "enable-task-2",
        "Транзитная ошибка",
        datetime.now(UTC),
    )

    assert task.status == EnableTaskStatus.CANCELLED
    assert task.last_error == "Отменена reconcile"
    session.commit.assert_not_called()


# enable_worker.mark_failed не перезаписывает CANCELLED-задачу
@pytest.mark.asyncio
async def test_mark_failed_skips_cancelled_task(monkeypatch):
    import run_enable_worker

    task = SimpleNamespace(
        id="enable-task-3",
        ad_id="ad-uuid-3",
        status=EnableTaskStatus.CANCELLED,
        completed_at=datetime.now(UTC),
        last_error="Отменена reconcile",
        next_retry_at=None,
    )
    session = _build_session_with_task(task)
    _patch_session_factory(monkeypatch, session)

    await run_enable_worker.mark_failed("enable-task-3", "Все попытки исчерпаны")

    assert task.status == EnableTaskStatus.CANCELLED
    assert task.last_error == "Отменена reconcile"
    session.commit.assert_not_called()


# Контроль: неотменённая задача нормально проходит через mark_succeeded и commit
@pytest.mark.asyncio
async def test_mark_succeeded_processes_non_cancelled_task(monkeypatch):
    import run_enable_worker

    task = SimpleNamespace(
        id="enable-task-4",
        ad_id="ad-uuid-4",
        status=EnableTaskStatus.RUNNING,
        completed_at=None,
        last_error=None,
        next_retry_at=None,
        attempt_count=1,
        max_attempts=10,
    )

    # Здесь нужен двух-execute мок: сначала task, потом snapshot (None — отсутствует в БД).
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    session.commit = AsyncMock()

    task_result = MagicMock()
    task_result.scalar_one_or_none.return_value = task
    snap_result = MagicMock()
    snap_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(side_effect=[task_result, snap_result])

    factory = MagicMock(return_value=session)
    monkeypatch.setattr(run_enable_worker, "get_session_factory", lambda: factory)

    await run_enable_worker.mark_succeeded("enable-task-4")

    # Очередь должна выставить SUCCEEDED через PostgresTaskQueue.mark_succeeded.
    assert task.status == EnableTaskStatus.SUCCEEDED
    session.commit.assert_awaited_once()

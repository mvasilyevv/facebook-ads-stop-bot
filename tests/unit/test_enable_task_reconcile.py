# -*- coding: utf-8 -*-
"""Тесты согласования очереди включений."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.domain import EnableTaskStatus
from core.enable_tasks import reconcile_enable_tasks


@dataclass
class FakeTask:
    """Фейковая задача на включение."""

    id: str = "task-001"
    fb_ad_id: str = "ad-001"
    status: EnableTaskStatus = EnableTaskStatus.RUNNING
    attempt_count: int = 1
    max_attempts: int = 10
    created_at: datetime = datetime.now(UTC) - timedelta(minutes=10)
    updated_at: datetime = datetime.now(UTC) - timedelta(minutes=10)
    completed_at: datetime | None = None
    next_retry_at: datetime | None = None
    last_error: str | None = None


def _rows_result(rows):
    """Создаёт мок результата SQLAlchemy."""
    result = MagicMock()
    result.all.return_value = rows
    return result


def _scalars_result(rows):
    """Создаёт мок результата SQLAlchemy для scalars().all()."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    return result


# Проверяем, что зависшая RUNNING-enable-задача возвращается в RETRYING.
@pytest.mark.asyncio
async def test_reconcile_enable_tasks_retries_stale_running_task():
    now = datetime.now(UTC)
    stale_time = now - timedelta(minutes=10)
    task = FakeTask(created_at=stale_time, updated_at=stale_time)
    session = AsyncMock()
    session.scalar = AsyncMock(side_effect=[None, None])
    session.execute = AsyncMock(
        side_effect=[
            _rows_result([]),
            _rows_result([(task, None)]),
        ]
    )

    summary = await reconcile_enable_tasks(session, now=now)

    assert summary["retried"] == ["ad-001"]
    assert summary["failed"] == []
    assert task.status == EnableTaskStatus.RETRYING
    assert task.completed_at is None
    assert task.next_retry_at == now
    assert "зависла" in (task.last_error or "")


# Проверяем, что зависшая enable-задача с исчерпанными попытками получает FAILED.
@pytest.mark.asyncio
async def test_reconcile_enable_tasks_fails_stale_task_when_attempts_exhausted():
    now = datetime.now(UTC)
    stale_time = now - timedelta(minutes=10)
    task = FakeTask(
        attempt_count=10,
        max_attempts=10,
        created_at=stale_time,
        updated_at=stale_time,
    )
    session = AsyncMock()
    session.scalar = AsyncMock(side_effect=[None, None])
    session.execute = AsyncMock(
        side_effect=[
            _rows_result([]),
            _rows_result([(task, None)]),
        ]
    )

    summary = await reconcile_enable_tasks(session, now=now)

    assert summary["retried"] == []
    assert summary["failed"] == ["ad-001"]
    assert task.status == EnableTaskStatus.FAILED
    assert task.completed_at == now
    assert task.next_retry_at is None
    assert "исчерпала лимит" in (task.last_error or "")


# Проверяем, что активная enable-задача из прошлых суток отменяется после zero-scan новых суток.
@pytest.mark.asyncio
async def test_reconcile_enable_tasks_cancels_previous_cabinet_day_task():
    now = datetime.now(UTC)
    task = FakeTask(
        status=EnableTaskStatus.PENDING,
        created_at=now - timedelta(minutes=5),
        updated_at=now - timedelta(minutes=5),
    )
    task.recommendation_event_id = "event-001"
    stale_event = MagicMock(live_batch_started_at=now - timedelta(hours=2))
    session = AsyncMock()
    session.scalar = AsyncMock(side_effect=[now, now - timedelta(hours=1)])
    session.execute = AsyncMock(
        side_effect=[
            _rows_result([(task, stale_event)]),
            _rows_result([]),
            _rows_result([]),
            _rows_result([]),
        ]
    )

    summary = await reconcile_enable_tasks(session, now=now)

    assert summary["cancelled"] == ["ad-001"]
    assert task.status == EnableTaskStatus.CANCELLED
    assert task.completed_at == now
    assert task.next_retry_at is None
    assert "новые сутки кабинета" in (task.last_error or "")

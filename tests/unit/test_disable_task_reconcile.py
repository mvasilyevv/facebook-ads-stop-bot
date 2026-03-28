# -*- coding: utf-8 -*-
"""Тесты согласования очереди отключений."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.disable_tasks import reconcile_disable_tasks
from core.domain import AlertState, DisableTaskStatus


@dataclass
class FakeTask:
    """Фейковая задача на отключение."""

    id: str = "task-001"
    fb_ad_id: str = "ad-001"
    status: DisableTaskStatus = DisableTaskStatus.RUNNING
    attempt_count: int = 1
    max_attempts: int = 10
    created_at: datetime = datetime.now(UTC) - timedelta(minutes=10)
    updated_at: datetime = datetime.now(UTC) - timedelta(minutes=10)
    completed_at: datetime | None = None
    next_retry_at: datetime | None = None
    last_error: str | None = None


@dataclass
class FakeSnapshot:
    """Фейковый снэпшот объявления."""

    fb_ad_id: str = "ad-001"
    delivery_status: str = "ACTIVE"
    alert_state: AlertState = AlertState.CLAIMED


def _result(rows):
    """Создаёт мок SQLAlchemy-результата."""
    mock = MagicMock()
    mock.all.return_value = rows
    return mock


# Проверяем, что активная задача завершается автоматически, если observer уже увидел OFF
@pytest.mark.asyncio
async def test_reconcile_marks_off_ads_as_succeeded():
    now = datetime.now(UTC)
    task = FakeTask()
    snapshot = FakeSnapshot(delivery_status="OFF")
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _result([(task, snapshot)]),
            _result([]),
        ]
    )

    summary = await reconcile_disable_tasks(session, now=now)

    assert summary["completed"] == ["ad-001"]
    assert task.status == DisableTaskStatus.SUCCEEDED
    assert task.completed_at == now
    assert task.next_retry_at is None
    assert task.last_error is None
    assert snapshot.alert_state == AlertState.DISABLED
    session.flush.assert_awaited_once()


# Проверяем, что зависшая RUNNING-задача возвращается в RETRYING
@pytest.mark.asyncio
async def test_reconcile_retries_stale_running_task():
    now = datetime.now(UTC)
    task = FakeTask()
    snapshot = FakeSnapshot(delivery_status="ACTIVE")
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _result([]),
            _result([(task, snapshot)]),
        ]
    )

    summary = await reconcile_disable_tasks(session, now=now)

    assert summary["retried"] == ["ad-001"]
    assert task.status == DisableTaskStatus.RETRYING
    assert task.completed_at is None
    assert task.next_retry_at == now
    assert "зависла" in (task.last_error or "")
    session.flush.assert_not_awaited()


# Проверяем, что зависшая задача с исчерпанными попытками получает FAILED
@pytest.mark.asyncio
async def test_reconcile_fails_stale_task_when_attempts_exhausted():
    now = datetime.now(UTC)
    task = FakeTask(attempt_count=10, max_attempts=10)
    snapshot = FakeSnapshot(delivery_status="ACTIVE")
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _result([]),
            _result([(task, snapshot)]),
        ]
    )

    summary = await reconcile_disable_tasks(session, now=now)

    assert summary["failed"] == ["ad-001"]
    assert task.status == DisableTaskStatus.FAILED
    assert task.completed_at == now
    assert task.next_retry_at is None
    assert "исчерпала лимит" in (task.last_error or "")

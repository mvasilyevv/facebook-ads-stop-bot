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
    session.scalar = AsyncMock(return_value=None)
    session.execute = AsyncMock(
        side_effect=[
            _result([(task, snapshot)]),
            _result([]),
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


# Проверяем, что OFF + SUCCEEDED автоматически чинится в DISABLED даже после старого сброса в NORMAL
@pytest.mark.asyncio
async def test_reconcile_repairs_off_snapshot_with_succeeded_task():
    now = datetime.now(UTC)
    snapshot = FakeSnapshot(delivery_status="OFF", alert_state=AlertState.NORMAL)
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    session.execute = AsyncMock(
        side_effect=[
            _result([]),
            _result([(snapshot, "task-001")]),
            _result([]),
        ]
    )

    summary = await reconcile_disable_tasks(session, now=now)

    assert summary["repaired"] == ["ad-001"]
    assert snapshot.alert_state == AlertState.DISABLED
    session.flush.assert_awaited_once()


# Проверяем, что зависшая RUNNING-задача возвращается в RETRYING
@pytest.mark.asyncio
async def test_reconcile_retries_stale_running_task():
    now = datetime.now(UTC)
    task = FakeTask()
    snapshot = FakeSnapshot(delivery_status="ACTIVE")
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    session.execute = AsyncMock(
        side_effect=[
            _result([]),
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
    session.scalar = AsyncMock(return_value=None)
    session.execute = AsyncMock(
        side_effect=[
            _result([]),
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


# Проверяем, что архивное объявление снимается с очереди и не уходит в retry
@pytest.mark.asyncio
async def test_reconcile_cancels_task_for_archived_snapshot():
    now = datetime.now(UTC)
    task = FakeTask(status=DisableTaskStatus.RETRYING)
    snapshot = FakeSnapshot(delivery_status="UNKNOWN", alert_state=AlertState.CLAIMED)
    snapshot.open_state_token = "token-archived"

    session = AsyncMock()
    session.scalar = AsyncMock(return_value=now)
    session.execute = AsyncMock(
        side_effect=[
            _result([]),
            _result([]),
            _result([(task, snapshot)]),
            _result([]),
        ]
    )

    summary = await reconcile_disable_tasks(session, now=now)

    assert summary["cancelled"] == ["ad-001"]
    assert task.status == DisableTaskStatus.CANCELLED
    assert task.completed_at == now
    assert task.next_retry_at is None
    assert "актуальную скан-сессию" in (task.last_error or "")
    assert snapshot.alert_state == AlertState.NORMAL
    assert snapshot.open_state_token is None

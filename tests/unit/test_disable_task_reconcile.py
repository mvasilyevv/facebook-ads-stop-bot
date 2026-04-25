# -*- coding: utf-8 -*-
"""Тесты согласования очереди отключений."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.disable_tasks import (
    calculate_active_disable_cutoff,
    is_delivery_disabled,
    reconcile_disable_tasks,
)
from core.domain import AlertStage, AlertState, DisableTaskStatus


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
    requested_by_username: str | None = None


@dataclass
class FakeSnapshot:
    """Фейковый снэпшот объявления."""

    fb_ad_id: str = "ad-001"
    delivery_status: str = "ACTIVE"
    alert_state: AlertState = AlertState.CLAIMED
    current_stage: AlertStage | None = AlertStage.STOP
    open_state_token: str | None = "incident-token"


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
            _result([(task, snapshot, "ad-001")]),
            _result([]),
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


# Проверяем, что helper распознаёт локализованные disabled-статусы, пока база догоняет новую нормализацию.
def test_is_delivery_disabled_supports_localized_statuses():
    assert is_delivery_disabled("OFF") is True
    assert is_delivery_disabled("Выключено") is True
    assert is_delivery_disabled("NOT_DELIVERING") is False
    assert is_delivery_disabled("Показ кампании прекращен") is False
    assert is_delivery_disabled("Обработка") is False


# Проверяем, что активная задача завершается по локализованному disabled-статусу.
@pytest.mark.asyncio
async def test_reconcile_marks_localized_disabled_ads_as_succeeded():
    now = datetime.now(UTC)
    task = FakeTask()
    snapshot = FakeSnapshot(delivery_status="Выключено")
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    session.execute = AsyncMock(
        side_effect=[
            _result([(task, snapshot, "ad-001")]),
            _result([]),
            _result([]),
            _result([]),
        ]
    )

    summary = await reconcile_disable_tasks(session, now=now)

    assert summary["completed"] == ["ad-001"]
    assert task.status == DisableTaskStatus.SUCCEEDED
    assert task.completed_at == now
    assert snapshot.alert_state == AlertState.DISABLED


# Проверяем, что self-heal считает окно активности от текущего времени, если last_scan устарел.
def test_calculate_active_disable_cutoff_uses_now_for_stale_scan():
    now = datetime(2026, 4, 16, 18, 0, tzinfo=UTC)
    last_scan = now - timedelta(hours=12)

    cutoff = calculate_active_disable_cutoff(now=now, last_scan=last_scan)

    assert cutoff == now - timedelta(minutes=30)


# Проверяем, что при свежем scan окно активности остаётся привязанным к нему.
def test_calculate_active_disable_cutoff_preserves_fresh_scan_window():
    now = datetime(2026, 4, 16, 18, 0, tzinfo=UTC)
    last_scan = now

    cutoff = calculate_active_disable_cutoff(now=now, last_scan=last_scan)

    assert cutoff == last_scan - timedelta(minutes=30)


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
            _result([]),
        ]
    )

    summary = await reconcile_disable_tasks(session, now=now)

    assert summary["repaired"] == ["ad-001"]
    assert snapshot.alert_state == AlertState.DISABLED
    session.flush.assert_awaited_once()


# Проверяем, что авто-отключение снимается с очереди, если свежий снэпшот уже не STOP.
@pytest.mark.asyncio
async def test_reconcile_cancels_auto_disable_when_snapshot_downgrades_to_warning():
    now = datetime.now(UTC)
    task = FakeTask(
        status=DisableTaskStatus.RETRYING,
        requested_by_username="bot_auto_stop",
    )
    snapshot = FakeSnapshot(
        delivery_status="ACTIVE",
        alert_state=AlertState.CLAIMED,
        current_stage=AlertStage.WARNING,
    )
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    session.execute = AsyncMock(
        side_effect=[
            _result([(task, snapshot, "ad-001")]),
            _result([]),
            _result([]),
            _result([]),
        ]
    )

    summary = await reconcile_disable_tasks(session, now=now)

    assert summary["cancelled"] == ["ad-001"]
    assert task.status == DisableTaskStatus.CANCELLED
    assert task.completed_at == now
    assert task.next_retry_at is None
    assert "больше не находится в STOP" in (task.last_error or "")
    assert snapshot.alert_state == AlertState.WARNING_SENT
    assert snapshot.open_state_token == "incident-token"


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
            _result([]),
            _result([(task, snapshot, "ad-001")]),
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
            _result([]),
            _result([(task, snapshot, "ad-001")]),
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
            _result([(task, snapshot, "ad-001")]),
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


# Проверяем, что после FAILED задачи reconciler создаёт новую auto-задачу если объявление всё ещё активно
@pytest.mark.asyncio
async def test_reconcile_incidents_creates_auto_task_after_failed():
    from unittest.mock import patch

    now = datetime.now(UTC)

    snapshot = FakeSnapshot(delivery_status="ACTIVE", alert_state=AlertState.CLAIMED)
    snapshot.ad_id = "ad-uuid-001"
    snapshot.id = "snap-uuid-001"
    snapshot.fb_ad_id = "ad-001"
    snapshot.open_state_token = "incident-key-001"
    snapshot.current_stage = None
    snapshot.fb_ad = None
    snapshot.spend = 0
    snapshot.clicks = 0
    snapshot.cpc = None
    snapshot.cpm = None
    snapshot.frequency = None
    snapshot.leads = 0
    snapshot.cost_per_lead = None
    snapshot.registrations = 0
    snapshot.cost_per_registration = None
    snapshot.deposits = 0
    snapshot.stop_rule_codes = []
    snapshot.warning_rule_codes = []
    snapshot.early_signal_rule_codes = []

    session = AsyncMock()
    # last_scan scalar
    session.scalar = AsyncMock(
        side_effect=[
            now,  # func.max(AdSnapshot.last_observed_at)
            0,  # active_count (нет активных задач)
            None,  # latest_succeeded (нет успешных попыток)
            0,  # auto_attempts (ни одной auto-попытки ещё)
            None,  # latest_task
        ]
    )
    snapshots_result = MagicMock()
    snapshots_result.scalars.return_value.all.return_value = [snapshot]
    session.execute = AsyncMock(return_value=snapshots_result)

    created_flag = {"called": False}

    async def fake_create(sess, *, snapshot, incident_key, attempt_sequence):
        created_flag["called"] = True
        assert incident_key == "incident-key-001"
        assert attempt_sequence == 1
        return True

    from core.observer import disable_reconciler

    with (
        patch.object(disable_reconciler, "get_session_factory") as mock_factory,
        patch.object(disable_reconciler, "_create_auto_disable_task_for_snapshot", fake_create),
    ):
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value.return_value = mock_cm

        await disable_reconciler.reconcile_disable_incidents_after_scan()

    assert created_flag["called"], (
        "reconciler должен создать auto-задачу для активного объявления без задач"
    )


# Проверяем, что ручная отмена disable-задачи блокирует тихий автоповтор того же incident.
@pytest.mark.asyncio
async def test_reconcile_incidents_skips_cancelled_incident():
    from unittest.mock import patch

    now = datetime.now(UTC)

    snapshot = FakeSnapshot(delivery_status="ACTIVE", alert_state=AlertState.CLAIMED)
    snapshot.ad_id = "ad-uuid-001"
    snapshot.id = "snap-uuid-001"
    snapshot.fb_ad_id = "ad-001"
    snapshot.open_state_token = "incident-key-001"
    snapshot.current_stage = None
    snapshot.fb_ad = None

    latest_task = FakeTask(status=DisableTaskStatus.CANCELLED)
    session = AsyncMock()
    session.scalar = AsyncMock(
        side_effect=[
            now,
            0,
            None,
            1,
            latest_task,
        ]
    )
    snapshots_result = MagicMock()
    snapshots_result.scalars.return_value.all.return_value = [snapshot]
    session.execute = AsyncMock(return_value=snapshots_result)

    from core.observer import disable_reconciler

    create_mock = AsyncMock(return_value=True)
    with (
        patch.object(disable_reconciler, "get_session_factory") as mock_factory,
        patch.object(
            disable_reconciler,
            "_create_auto_disable_task_for_snapshot",
            create_mock,
        ),
    ):
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value.return_value = mock_cm

        alerts = await disable_reconciler.reconcile_disable_incidents_after_scan()

    assert alerts == []
    create_mock.assert_not_awaited()

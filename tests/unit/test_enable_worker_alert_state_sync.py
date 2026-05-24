# -*- coding: utf-8 -*-
"""Проверяет, что после успешного включения enable_worker.mark_succeeded
сбрасывает AdSnapshot.alert_state, чтобы UI не показывал устаревший DISABLED.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.domain import AlertState, EnableTaskStatus


def _build_session_factory(task, snapshot, monkeypatch) -> AsyncMock:
    """Подменяет get_session_factory: первый execute → task, второй → snapshot."""
    import run_enable_worker

    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    session.commit = AsyncMock()

    task_result = MagicMock()
    task_result.scalar_one_or_none.return_value = task

    snapshot_result = MagicMock()
    snapshot_result.scalar_one_or_none.return_value = snapshot

    session.execute = AsyncMock(side_effect=[task_result, snapshot_result])
    session.scalar = AsyncMock(return_value=None)

    factory = MagicMock(return_value=session)
    monkeypatch.setattr(run_enable_worker, "get_session_factory", lambda: factory)
    return session


# После успешного включения AdSnapshot.alert_state сбрасывается из DISABLED в NORMAL
@pytest.mark.asyncio
async def test_mark_succeeded_resets_disabled_alert_state(monkeypatch):
    import run_enable_worker

    task = SimpleNamespace(
        id="task-en-1",
        ad_id="ad-uuid-1",
        status=EnableTaskStatus.RUNNING,
        completed_at=None,
        last_error=None,
        next_retry_at=None,
        attempt_count=1,
        max_attempts=10,
    )
    snapshot = SimpleNamespace(
        ad_id="ad-uuid-1",
        alert_state=AlertState.DISABLED,
        open_state_token="incident-token-1",
    )

    session = _build_session_factory(task, snapshot, monkeypatch)

    await run_enable_worker.mark_succeeded("task-en-1")

    assert task.status == EnableTaskStatus.SUCCEEDED
    assert snapshot.alert_state == AlertState.NORMAL
    assert snapshot.open_state_token is None
    session.commit.assert_awaited_once()


# Если snapshot уже в NORMAL — не трогаем токен и состояние
@pytest.mark.asyncio
async def test_mark_succeeded_leaves_normal_snapshot_untouched(monkeypatch):
    import run_enable_worker

    task = SimpleNamespace(
        id="task-en-2",
        ad_id="ad-uuid-2",
        status=EnableTaskStatus.RUNNING,
        completed_at=None,
        last_error=None,
        next_retry_at=None,
        attempt_count=1,
        max_attempts=10,
    )
    snapshot = SimpleNamespace(
        ad_id="ad-uuid-2",
        alert_state=AlertState.NORMAL,
        open_state_token=None,
    )

    _build_session_factory(task, snapshot, monkeypatch)

    await run_enable_worker.mark_succeeded("task-en-2")

    assert task.status == EnableTaskStatus.SUCCEEDED
    assert snapshot.alert_state == AlertState.NORMAL
    assert snapshot.open_state_token is None


# Снэпшот в CLAIMED тоже сбрасывается, потому что после включения — никаких активных алертов
@pytest.mark.asyncio
async def test_mark_succeeded_resets_claimed_alert_state(monkeypatch):
    import run_enable_worker

    task = SimpleNamespace(
        id="task-en-3",
        ad_id="ad-uuid-3",
        status=EnableTaskStatus.RUNNING,
        completed_at=None,
        last_error=None,
        next_retry_at=None,
        attempt_count=1,
        max_attempts=10,
    )
    snapshot = SimpleNamespace(
        ad_id="ad-uuid-3",
        alert_state=AlertState.CLAIMED,
        open_state_token="incident-token-3",
    )

    _build_session_factory(task, snapshot, monkeypatch)

    await run_enable_worker.mark_succeeded("task-en-3")

    assert snapshot.alert_state == AlertState.NORMAL
    assert snapshot.open_state_token is None

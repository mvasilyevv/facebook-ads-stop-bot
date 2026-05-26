# -*- coding: utf-8 -*-
"""Тесты reconciler'а core/meta_api/reconciler.py."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.meta_api.reconciler import (
    reconcile_all,
    reconcile_expired_drafts,
    reconcile_stuck_running,
)

# ─── Вспомогательные фабрики ───────────────────────────────────────────────


def _make_task(
    *,
    status: str = "DRAFT",
    mutation_kind: str = "pause_ad",
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> MagicMock:
    """Минимальный мок MetaApiMutationTask."""
    task = MagicMock()
    task.id = uuid.uuid4()
    task.status = status
    task.mutation_kind = mutation_kind
    task.created_at = created_at or datetime.now(UTC)
    task.updated_at = updated_at or datetime.now(UTC)
    task.last_error = None
    task.completed_at = None
    task.next_retry_at = None
    task.attempt_count = 0
    return task


def _make_db_with_tasks(tasks: list) -> AsyncMock:
    """Мок AsyncSession, .execute() возвращает заданный список задач."""
    execute_result = AsyncMock()
    scalars_mock = MagicMock()
    scalars_mock.__iter__ = MagicMock(return_value=iter(tasks))
    execute_result.scalars = MagicMock(return_value=scalars_mock)

    db = AsyncMock()
    db.execute = AsyncMock(return_value=execute_result)
    db.flush = AsyncMock()
    return db


# ─── reconcile_expired_drafts ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reconcile_expired_drafts_cancels_old():
    """DRAFT задачи старше max_age_hours должны стать CANCELLED."""
    old_draft = _make_task(
        status="DRAFT",
        created_at=datetime.now(UTC) - timedelta(hours=25),
    )
    db = _make_db_with_tasks([old_draft])

    count = await reconcile_expired_drafts(db, max_age_hours=24)

    assert count == 1
    assert old_draft.status == "CANCELLED"
    assert old_draft.completed_at is not None
    assert "автоматически отменён" in (old_draft.last_error or "")
    db.flush.assert_called_once()


@pytest.mark.asyncio
async def test_reconcile_expired_drafts_keeps_fresh():
    """DRAFT задачи моложе max_age_hours не должны затрагиваться."""
    # Свежий черновик — не попадает в WHERE-фильтр → возвращаем пустой список
    db = _make_db_with_tasks([])

    count = await reconcile_expired_drafts(db, max_age_hours=24)

    assert count == 0
    db.flush.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_expired_drafts_returns_count():
    """Возвращает количество отменённых задач."""
    tasks = [
        _make_task(status="DRAFT", created_at=datetime.now(UTC) - timedelta(hours=30)),
        _make_task(status="DRAFT", created_at=datetime.now(UTC) - timedelta(hours=48)),
    ]
    db = _make_db_with_tasks(tasks)

    count = await reconcile_expired_drafts(db, max_age_hours=24)

    assert count == 2
    for task in tasks:
        assert task.status == "CANCELLED"


@pytest.mark.asyncio
async def test_reconcile_expired_drafts_no_tasks():
    """Если задач нет — возвращает 0 без flush."""
    db = _make_db_with_tasks([])

    count = await reconcile_expired_drafts(db)

    assert count == 0
    db.flush.assert_not_called()


# ─── reconcile_stuck_running ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reconcile_stuck_running_restores_pending():
    """RUNNING задачи зависшие дольше max_running_minutes → PENDING."""
    stuck = _make_task(
        status="RUNNING",
        updated_at=datetime.now(UTC) - timedelta(minutes=35),
    )
    db = _make_db_with_tasks([stuck])

    count = await reconcile_stuck_running(db, max_running_minutes=30)

    assert count == 1
    assert stuck.status == "PENDING"
    assert stuck.next_retry_at is not None
    assert "зависла в RUNNING" in (stuck.last_error or "")
    db.flush.assert_called_once()


@pytest.mark.asyncio
async def test_reconcile_stuck_running_keeps_fresh():
    """RUNNING задачи в допустимом временном окне не трогаются."""
    # Нет задач старше лимита — возвращаем пустой список
    db = _make_db_with_tasks([])

    count = await reconcile_stuck_running(db, max_running_minutes=30)

    assert count == 0
    db.flush.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_stuck_running_multiple():
    """Несколько зависших задач — все восстанавливаются."""
    tasks = [
        _make_task(status="RUNNING", updated_at=datetime.now(UTC) - timedelta(minutes=60)),
        _make_task(status="RUNNING", updated_at=datetime.now(UTC) - timedelta(minutes=45)),
    ]
    db = _make_db_with_tasks(tasks)

    count = await reconcile_stuck_running(db, max_running_minutes=30)

    assert count == 2
    for task in tasks:
        assert task.status == "PENDING"


# ─── reconcile_all ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reconcile_all_combines_results():
    """reconcile_all запускает оба шага и возвращает агрегированные счётчики."""
    # Патчим оба шага — проверяем что оба вызваны и результаты объединены
    with (
        patch(
            "core.meta_api.reconciler.reconcile_expired_drafts",
            new=AsyncMock(return_value=3),
        ) as mock_drafts,
        patch(
            "core.meta_api.reconciler.reconcile_stuck_running",
            new=AsyncMock(return_value=2),
        ) as mock_running,
    ):
        db = AsyncMock()
        result = await reconcile_all(db)

    mock_drafts.assert_called_once_with(db)
    mock_running.assert_called_once_with(db)
    assert result == {"expired_drafts": 3, "stuck_running": 2}


@pytest.mark.asyncio
async def test_reconcile_all_zeros():
    """reconcile_all с нулевыми результатами — возвращает нулевые счётчики."""
    with (
        patch(
            "core.meta_api.reconciler.reconcile_expired_drafts",
            new=AsyncMock(return_value=0),
        ),
        patch(
            "core.meta_api.reconciler.reconcile_stuck_running",
            new=AsyncMock(return_value=0),
        ),
    ):
        db = AsyncMock()
        result = await reconcile_all(db)

    assert result == {"expired_drafts": 0, "stuck_running": 0}

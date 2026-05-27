# -*- coding: utf-8 -*-
"""Интеграционные тесты reconciler_worker."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from apps.reconciler_worker.worker import cancel_old_drafts, reconcile_stuck_running


def _db_url() -> str | None:
    try:
        from core.config import get_settings

        return get_settings().database_url
    except Exception:
        return None


# Проверка: зависшая 'running' с старым updated_at переводится в 'retrying'
@pytest.mark.asyncio
async def test_reconcile_stuck_running() -> None:
    url = _db_url()
    if not url:
        pytest.skip("DB URL не доступен")

    engine = create_async_engine(url)
    stuck_key = f"test_reconciler_stuck_{uuid.uuid4().hex[:8]}"
    try:
        # Вставляем running-задачу с очень старым updated_at (60 минут назад)
        old_ts = datetime.now(timezone.utc) - timedelta(minutes=60)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO task_queue
                        (task_type, status, idempotency_key, payload, requested_by, created_at, updated_at)
                    VALUES
                        ('disable', 'running', :k, CAST('{}' AS JSONB), 'test', :ts, :ts)
                    """
                ),
                {"k": stuck_key, "ts": old_ts},
            )

        moved = await reconcile_stuck_running(engine)
        assert moved >= 1

        async with engine.connect() as conn:
            status = (
                await conn.execute(
                    text("SELECT status, attempt_count FROM task_queue WHERE idempotency_key = :k"),
                    {"k": stuck_key},
                )
            ).first()
            assert status is not None
            assert status[0] == "retrying"
            assert status[1] == 1
    finally:
        # Очистка
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM task_queue WHERE idempotency_key = :k"),
                {"k": stuck_key},
            )
        await engine.dispose()


# Проверка: старый draft (>24h) → cancelled
@pytest.mark.asyncio
async def test_cancel_old_drafts() -> None:
    url = _db_url()
    if not url:
        pytest.skip("DB URL не доступен")

    engine = create_async_engine(url)
    old_draft_key = f"test_reconciler_draft_{uuid.uuid4().hex[:8]}"
    try:
        old_ts = datetime.now(timezone.utc) - timedelta(hours=30)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO task_queue
                        (task_type, status, idempotency_key, payload, requested_by, created_at, updated_at)
                    VALUES
                        ('meta_api_mutation', 'draft', :k, CAST('{}' AS JSONB), 'ai_draft', :ts, :ts)
                    """
                ),
                {"k": old_draft_key, "ts": old_ts},
            )

        cancelled = await cancel_old_drafts(engine)
        assert cancelled >= 1

        async with engine.connect() as conn:
            status = (
                await conn.execute(
                    text("SELECT status FROM task_queue WHERE idempotency_key = :k"),
                    {"k": old_draft_key},
                )
            ).first()
            assert status is not None
            assert status[0] == "cancelled"
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM task_queue WHERE idempotency_key = :k"),
                {"k": old_draft_key},
            )
        await engine.dispose()

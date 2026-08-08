# -*- coding: utf-8 -*-
"""Интеграционные тесты reconciler_worker."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from apps.reconciler_worker.worker import reconcile_stuck_running


# Проверка: зависшая 'running' с старым updated_at переводится в 'retrying'
@pytest.mark.asyncio
async def test_reconcile_stuck_running(pg_engine) -> None:
    engine = pg_engine
    stuck_key = f"test_reconciler_stuck_{uuid.uuid4().hex[:8]}"
    try:
        # Вставляем running-задачу с очень старым updated_at (60 минут назад)
        old_ts = datetime.now(timezone.utc) - timedelta(minutes=60)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO task_queue
                        (task_type, status, idempotency_key, payload, requested_by,
                         lane, created_at, updated_at)
                    VALUES
                        ('observer_scan', 'running', :k,
                         CAST('{"source":"test"}' AS JSONB), 'test', 'interactive',
                         :ts, :ts)
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
        # Очистка (engine закроет pg_engine-фикстура)
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM task_queue WHERE idempotency_key = :k"),
                {"k": stuck_key},
            )

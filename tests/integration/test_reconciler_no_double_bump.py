# -*- coding: utf-8 -*-
"""Регрессия на CRIT #3: reconciler инкрементит attempt_count только один раз.

До фикса в apps/reconciler_worker/worker.py был дубль SQL-логики из
core/tasks/queue.reconcile_stuck_running. Оба делали attempt_count = +1
(сначала worker.py, потом каноническая через delete-recreate path), и
max_attempts=5 исчерпывался за ~2 фактических попытки.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from apps.reconciler_worker.worker import run_once
from core.tasks import create_task
from core.tasks.queue import reconcile_stuck_running as canonical_reconcile


@pytest_asyncio.fixture
async def clean_task_queue(pg_engine):
    """Чистка task_queue до и после теста."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue"))

    await _truncate()
    yield
    await _truncate()


# Канонический reconcile_stuck_running делает ровно +1 к attempt_count (а не +2 как было).
@pytest.mark.asyncio
async def test_canonical_reconcile_single_bump(pg_engine, clean_task_queue) -> None:
    task_id = await create_task(
        pg_engine,
        task_type="disable",
        idempotency_key=f"bump-{uuid.uuid4().hex[:8]}",
        payload={"fb_ad_id": "12345"},
        requested_by="test",
        max_attempts=5,
    )
    assert task_id is not None

    # Эмулируем stuck-running с уже накопленным attempt_count=2
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'running',
                    attempt_count = 2,
                    updated_at = NOW() - INTERVAL '2 hours'
                WHERE id = :i
                """
            ),
            {"i": task_id},
        )

    n = await canonical_reconcile(pg_engine, stuck_after_seconds=1800)
    assert n >= 1

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, attempt_count FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row is not None
    assert row[0] == "retrying"
    # Главное: один bump = 2 + 1 = 3 (а не 4 от дубля).
    assert row[1] == 3, f"ожидали attempt_count=3 (2 + 1 bump), получили {row[1]}"


# apps.reconciler_worker.run_once делегирует в canonical → тоже один bump, не два.
@pytest.mark.asyncio
async def test_reconciler_worker_delegates_no_double_bump(pg_engine, clean_task_queue) -> None:
    task_id = await create_task(
        pg_engine,
        task_type="disable",
        idempotency_key=f"deleg-{uuid.uuid4().hex[:8]}",
        payload={"fb_ad_id": "67890"},
        requested_by="test",
    )
    assert task_id is not None

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'running',
                    attempt_count = 1,
                    updated_at = NOW() - INTERVAL '2 hours'
                WHERE id = :i
                """
            ),
            {"i": task_id},
        )

    counts = await run_once(pg_engine)
    assert counts.get("stuck_to_retrying", 0) >= 1

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, attempt_count FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row[0] == "retrying"
    # 1 + 1 канонический bump = 2; если бы остался дубль worker.py, было бы 3.
    assert row[1] == 2, f"ожидали attempt_count=2 (1 + 1 bump), получили {row[1]}"


# Повторный run_once в течение того же стресс-окна не bump'ит уже-retrying task.
@pytest.mark.asyncio
async def test_reconciler_second_run_no_bump_on_retrying(
    pg_engine,
    clean_task_queue,
) -> None:
    task_id = await create_task(
        pg_engine,
        task_type="disable",
        idempotency_key=f"twice-{uuid.uuid4().hex[:8]}",
        payload={"fb_ad_id": "999"},
        requested_by="test",
    )
    assert task_id is not None

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'running',
                    attempt_count = 0,
                    updated_at = NOW() - INTERVAL '2 hours'
                WHERE id = :i
                """
            ),
            {"i": task_id},
        )

    # Первый прогон: stuck → retrying, bump до 1.
    await run_once(pg_engine)

    # Второй прогон сразу: статус уже 'retrying', WHERE требует 'running' →
    # никакого update, никакого второго bump.
    counts2 = await run_once(pg_engine)
    assert counts2.get("stuck_to_retrying", 0) == 0

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, attempt_count FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row[0] == "retrying"
    assert row[1] == 1, "повторный run не должен bump'ать уже-retrying task"

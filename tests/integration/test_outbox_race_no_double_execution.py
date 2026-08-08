# -*- coding: utf-8 -*-
"""Race-сценарий: после reconciler-таймаута зомби-worker A не должен
затирать результат worker B, который успел перехватить и завершить задачу.

Это регрессия на CRIT #2 (security audit раунда 5): mark_succeeded/mark_failed
без WHERE status='running' downgrad'или succeeded/failed обратно после гонки.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.tasks import (
    claim_next_task,
    create_task,
    mark_succeeded,
    reconcile_stuck_running,
)


@pytest_asyncio.fixture
async def clean_task_queue(pg_engine):
    """Чистка task_queue до и после теста — изоляция от других сценариев."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue"))

    await _truncate()
    yield
    await _truncate()


# Полный сценарий гонки: worker A завис → reconciler → claim worker B → mark_succeeded B →
# mark_succeeded A (зомби) должен вернуть False и НЕ изменить status/result в БД.
@pytest.mark.asyncio
async def test_zombie_worker_after_reconciler_does_not_overwrite_success(
    pg_engine,
    clean_task_queue,
) -> None:
    fb_ad_id = f"23000{uuid.uuid4().hex[:8]}"
    task_id = await create_task(
        pg_engine,
        task_type="observer_scan",
        idempotency_key=f"race-{fb_ad_id}",
        payload={"source": "test", "target_id": fb_ad_id},
        requested_by="test",
        max_attempts=5,
    )
    assert task_id is not None

    # Worker A захватил задачу — status='running', attempt_count=0
    claim_a = await claim_next_task(pg_engine, task_type="observer_scan")
    assert claim_a.task is not None
    assert claim_a.task.id == task_id
    assert claim_a.task.status == "running"

    # Симулируем зависший worker A: updated_at в прошлом на 2 часа.
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE task_queue SET updated_at = NOW() - INTERVAL '2 hours', "
                "lease_expires_at = NOW() - INTERVAL '1 second' WHERE id = :i"
            ),
            {"i": task_id},
        )

    # Reconciler видит stuck running → переводит в retrying (+1 к attempt_count)
    moved = await reconcile_stuck_running(pg_engine, stuck_after_seconds=1800)
    assert moved >= 1

    # Worker B перехватывает (status='retrying' → 'running')
    claim_b = await claim_next_task(pg_engine, task_type="observer_scan")
    assert claim_b.task is not None
    assert claim_b.task.id == task_id
    assert claim_b.task.attempt_count == 1  # после reconciler-bump

    # Worker B завершает успешно — это «настоящий» результат
    applied_b = await mark_succeeded(
        pg_engine,
        task_id=task_id,
        result={"fb_ad_id": fb_ad_id, "final_state": "false", "by": "worker_B"},
        lease_owner=claim_b.task.lease_owner,
        lease_token=claim_b.task.lease_token,
    )
    assert applied_b is True

    # Зомби worker A ВДРУГ дышит и пытается mark_succeeded — должен вернуть False
    # потому что status уже 'succeeded', а WHERE требует 'running'.
    applied_a = await mark_succeeded(
        pg_engine,
        task_id=task_id,
        result={"fb_ad_id": fb_ad_id, "final_state": "ZOMBIE_VALUE", "by": "worker_A"},
        lease_owner=claim_a.task.lease_owner,
        lease_token=claim_a.task.lease_token,
    )
    assert applied_a is False, "зомби worker A не должен применять mark_succeeded"

    # Финал: status='succeeded', result от worker B (не от зомби A),
    # attempt_count=1 (один bump от reconciler, без двойного).
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, result, attempt_count FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row is not None
    assert row[0] == "succeeded"
    assert row[1]["by"] == "worker_B"  # зомби не перезаписал
    assert row[1]["final_state"] == "false"
    assert row[2] == 1


# Симметричный тест: worker B сделал mark_failed (permanent error), зомби A не должен
# downgrade'нуть failed → succeeded (это страшнее даже чем потеря result).
@pytest.mark.asyncio
async def test_zombie_worker_does_not_downgrade_failed_to_succeeded(
    pg_engine,
    clean_task_queue,
) -> None:
    fb_ad_id = f"24000{uuid.uuid4().hex[:8]}"
    task_id = await create_task(
        pg_engine,
        task_type="tracker_event_process",
        idempotency_key=f"race-fail-{fb_ad_id}",
        payload={"source": "test", "target_id": fb_ad_id},
        requested_by="test",
        max_attempts=5,
    )
    assert task_id is not None

    # Worker A захватил
    claim_a = await claim_next_task(pg_engine, task_type="tracker_event_process")
    assert claim_a.task is not None

    # Stuck → reconciler → retrying
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE task_queue SET updated_at = NOW() - INTERVAL '2 hours', "
                "lease_expires_at = NOW() - INTERVAL '1 second' WHERE id = :i"
            ),
            {"i": task_id},
        )
    await reconcile_stuck_running(pg_engine, stuck_after_seconds=1800)

    # Worker B захватил и завершил FAILED (permanent ошибка)
    claim_b = await claim_next_task(pg_engine, task_type="tracker_event_process")
    assert claim_b.task is not None

    from core.tasks import mark_failed

    applied_b = await mark_failed(
        pg_engine,
        task_id=task_id,
        error="ad permanently deleted",
        lease_owner=claim_b.task.lease_owner,
        lease_token=claim_b.task.lease_token,
    )
    assert applied_b is True

    # Зомби A приходит с success — НЕ должен downgrade'нуть failed → succeeded
    applied_a = await mark_succeeded(
        pg_engine,
        task_id=task_id,
        result={"final_state": "true", "by": "zombie"},
        lease_owner=claim_a.task.lease_owner,
        lease_token=claim_a.task.lease_token,
    )
    assert applied_a is False

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, last_error FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row[0] == "failed"
    assert "ad permanently deleted" in (row[1] or "")

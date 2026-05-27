# -*- coding: utf-8 -*-
"""Интеграционные тесты для core.tasks.queue — реальная БД.

Покрывает контракты: idempotency_key, FOR UPDATE SKIP LOCKED, exponential backoff,
reconcile stuck running, cancel stale drafts.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.tasks import (
    cancel_stale_drafts,
    claim_next_task,
    create_task,
    mark_succeeded,
    reconcile_stuck_running,
    requeue_for_retry,
)
from core.tasks.queue import get_task_by_idempotency_key


@pytest_asyncio.fixture
async def clean_task_queue(pg_engine):
    """Чистит task_queue до и после теста, чтобы тесты не пересекались."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue"))

    await _truncate()
    yield
    await _truncate()


# Сценарий: create_task возвращает id, повтор с тем же idempotency_key → None
@pytest.mark.asyncio
async def test_create_task_idempotent(pg_engine, clean_task_queue) -> None:
    key = f"idem-{uuid.uuid4().hex[:8]}"
    payload = {"fb_ad_id": "12345"}

    first_id = await create_task(
        pg_engine,
        task_type="disable",
        idempotency_key=key,
        payload=payload,
        requested_by="test",
    )
    assert first_id is not None and first_id > 0

    second_id = await create_task(
        pg_engine,
        task_type="disable",
        idempotency_key=key,
        payload=payload,
        requested_by="test",
    )
    assert second_id is None

    # И получить тот же row по ключу
    task = await get_task_by_idempotency_key(pg_engine, idempotency_key=key)
    assert task is not None
    assert task.id == first_id
    assert task.payload["fb_ad_id"] == "12345"
    assert task.status == "pending"


# Сценарий: claim атомарно переводит pending → running
@pytest.mark.asyncio
async def test_claim_marks_running(pg_engine, clean_task_queue) -> None:
    key = f"claim-{uuid.uuid4().hex[:8]}"
    await create_task(
        pg_engine,
        task_type="enable",
        idempotency_key=key,
        payload={"fb_ad_id": "999"},
        requested_by="test",
    )

    claim = await claim_next_task(pg_engine, task_type="enable")
    assert claim.queue_empty is False
    assert claim.task is not None
    assert claim.task.status == "running"
    assert claim.task.payload["fb_ad_id"] == "999"

    # Второй claim того же типа — пусто (уже захвачено)
    second = await claim_next_task(pg_engine, task_type="enable")
    assert second.queue_empty is True
    assert second.task is None


# Сценарий: claim не трогает задачи другого task_type
@pytest.mark.asyncio
async def test_claim_filters_by_type(pg_engine, clean_task_queue) -> None:
    await create_task(
        pg_engine,
        task_type="disable",
        idempotency_key=f"d-{uuid.uuid4().hex[:6]}",
        payload={"fb_ad_id": "1"},
        requested_by="test",
    )
    await create_task(
        pg_engine,
        task_type="enable",
        idempotency_key=f"e-{uuid.uuid4().hex[:6]}",
        payload={"fb_ad_id": "2"},
        requested_by="test",
    )

    # Воркер enable не должен схватить disable-задачу
    disable_claim = await claim_next_task(pg_engine, task_type="disable")
    enable_claim = await claim_next_task(pg_engine, task_type="enable")

    assert disable_claim.task.payload["fb_ad_id"] == "1"
    assert enable_claim.task.payload["fb_ad_id"] == "2"


# Сценарий: mark_succeeded ставит completed_at и status
@pytest.mark.asyncio
async def test_mark_succeeded(pg_engine, clean_task_queue) -> None:
    key = f"ok-{uuid.uuid4().hex[:8]}"
    task_id = await create_task(
        pg_engine,
        task_type="disable",
        idempotency_key=key,
        payload={"fb_ad_id": "1"},
        requested_by="test",
    )
    await claim_next_task(pg_engine, task_type="disable")
    await mark_succeeded(pg_engine, task_id=task_id, result={"final_state": "false"})

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, completed_at, result FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row[0] == "succeeded"
    assert row[1] is not None
    assert row[2] == {"final_state": "false"}


# Сценарий: requeue_for_retry — exponential backoff + attempt_count++
@pytest.mark.asyncio
async def test_requeue_increments_attempts_and_sets_next_retry(pg_engine, clean_task_queue) -> None:
    key = f"retry-{uuid.uuid4().hex[:8]}"
    task_id = await create_task(
        pg_engine,
        task_type="disable",
        idempotency_key=key,
        payload={"fb_ad_id": "1"},
        requested_by="test",
        max_attempts=5,
    )
    claim = await claim_next_task(pg_engine, task_type="disable")
    assert claim.task is not None

    retried = await requeue_for_retry(
        pg_engine,
        task_id=task_id,
        error="network glitch",
        attempt_count=claim.task.attempt_count,
        max_attempts=claim.task.max_attempts,
    )
    assert retried is True

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT status, attempt_count, next_retry_at, last_error "
                    "FROM task_queue WHERE id = :i"
                ),
                {"i": task_id},
            )
        ).first()
    assert row[0] == "retrying"
    assert row[1] == 1
    assert row[2] is not None
    assert "network glitch" in row[3]


# Сценарий: исчерпан max_attempts → status='failed', а не retrying
@pytest.mark.asyncio
async def test_requeue_marks_failed_at_max_attempts(pg_engine, clean_task_queue) -> None:
    key = f"fail-{uuid.uuid4().hex[:8]}"
    task_id = await create_task(
        pg_engine,
        task_type="disable",
        idempotency_key=key,
        payload={"fb_ad_id": "1"},
        requested_by="test",
        max_attempts=3,
    )
    await claim_next_task(pg_engine, task_type="disable")

    # При attempt_count=2 + max_attempts=3 → новая попытка = 3 = max_attempts → failed
    retried = await requeue_for_retry(
        pg_engine,
        task_id=task_id,
        error="persistent error",
        attempt_count=2,
        max_attempts=3,
    )
    assert retried is False

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, last_error FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row[0] == "failed"
    assert "persistent error" in row[1]


# Сценарий: reconcile_stuck_running восстанавливает зависшие 'running' старше threshold
@pytest.mark.asyncio
async def test_reconcile_stuck_running(pg_engine, clean_task_queue) -> None:
    key = f"stuck-{uuid.uuid4().hex[:8]}"
    task_id = await create_task(
        pg_engine,
        task_type="disable",
        idempotency_key=key,
        payload={"fb_ad_id": "1"},
        requested_by="test",
    )
    await claim_next_task(pg_engine, task_type="disable")

    # Симулируем что воркер «крашнулся» 1 час назад
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE task_queue SET updated_at = NOW() - interval '1 hour' WHERE id = :i"),
            {"i": task_id},
        )

    n = await reconcile_stuck_running(pg_engine, stuck_after_seconds=1800)
    assert n >= 1

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, last_error FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row[0] == "retrying"
    assert "stuck timeout" in (row[1] or "")


# Сценарий: cancel_stale_drafts отменяет drafts старше 24h
@pytest.mark.asyncio
async def test_cancel_stale_drafts(pg_engine, clean_task_queue) -> None:
    key = f"draft-{uuid.uuid4().hex[:8]}"
    task_id = await create_task(
        pg_engine,
        task_type="meta_api_mutation",
        idempotency_key=key,
        payload={"ad_id": "1"},
        requested_by="ai",
        status="draft",
    )

    # Симулируем что draft создан 25h назад
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE task_queue SET created_at = NOW() - interval '25 hours' WHERE id = :i"),
            {"i": task_id},
        )

    n = await cancel_stale_drafts(pg_engine, older_than_seconds=24 * 3600)
    assert n >= 1

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, completed_at FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row[0] == "cancelled"
    assert row[1] is not None


# Сценарий: claim не возвращает задачу с next_retry_at в будущем
@pytest.mark.asyncio
async def test_claim_skips_future_retry(pg_engine, clean_task_queue) -> None:
    key = f"future-{uuid.uuid4().hex[:8]}"
    task_id = await create_task(
        pg_engine,
        task_type="disable",
        idempotency_key=key,
        payload={"fb_ad_id": "1"},
        requested_by="test",
    )
    # Ставим next_retry_at в будущее и status='retrying'
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE task_queue SET status = 'retrying', "
                "next_retry_at = NOW() + interval '1 hour' WHERE id = :i"
            ),
            {"i": task_id},
        )

    claim = await claim_next_task(pg_engine, task_type="disable")
    assert claim.queue_empty is True
    assert claim.task is None

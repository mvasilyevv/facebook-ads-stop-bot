# -*- coding: utf-8 -*-
"""Контракт bool-возврата mark_succeeded/mark_failed.

True — UPDATE применился (status был 'running').
False — UPDATE не применился (status уже 'succeeded'/'failed'/'cancelled'),
       что означает race с другим воркером (CRIT #2 fix).
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.tasks import Task, claim_next_task, create_task, mark_failed, mark_succeeded


@pytest_asyncio.fixture
async def clean_task_queue(pg_engine):
    """Чистка task_queue до и после теста."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue"))

    await _truncate()
    yield
    await _truncate()


async def _create_running_task(pg_engine, *, task_type: str = "observer_scan") -> Task:
    """Хелпер: create + claim → fenced task в status='running'."""
    task_id = await create_task(
        pg_engine,
        task_type=task_type,
        idempotency_key=f"bool-{uuid.uuid4().hex[:8]}",
        payload={"source": "test"},
        requested_by="test",
    )
    assert task_id is not None
    claim = await claim_next_task(pg_engine, task_type=task_type)
    assert claim.task is not None
    return claim.task


# mark_succeeded на status='running' → True (нормальный happy-path).
@pytest.mark.asyncio
async def test_mark_succeeded_on_running_returns_true(pg_engine, clean_task_queue) -> None:
    task = await _create_running_task(pg_engine)
    applied = await mark_succeeded(
        pg_engine,
        task_id=task.id,
        result={"ok": True},
        lease_owner=task.lease_owner,
        lease_token=task.lease_token,
    )
    assert applied is True


# mark_succeeded дважды подряд — второй вызов вернёт False (status уже 'succeeded').
@pytest.mark.asyncio
async def test_mark_succeeded_twice_second_returns_false(pg_engine, clean_task_queue) -> None:
    task = await _create_running_task(pg_engine)
    fence = {"lease_owner": task.lease_owner, "lease_token": task.lease_token}
    first = await mark_succeeded(pg_engine, task_id=task.id, **fence)
    second = await mark_succeeded(pg_engine, task_id=task.id, **fence)
    assert first is True
    assert second is False


# mark_succeeded на status='failed' → False, статус не меняется.
@pytest.mark.asyncio
async def test_mark_succeeded_on_failed_returns_false(pg_engine, clean_task_queue) -> None:
    task = await _create_running_task(pg_engine)
    fence = {"lease_owner": task.lease_owner, "lease_token": task.lease_token}
    await mark_failed(pg_engine, task_id=task.id, error="boom", **fence)

    applied = await mark_succeeded(pg_engine, task_id=task.id, result={"ok": True}, **fence)
    assert applied is False

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status FROM task_queue WHERE id = :i"),
                {"i": task.id},
            )
        ).first()
    assert row[0] == "failed"


# mark_failed на status='running' → True (нормальный happy-path).
@pytest.mark.asyncio
async def test_mark_failed_on_running_returns_true(pg_engine, clean_task_queue) -> None:
    task = await _create_running_task(pg_engine)
    applied = await mark_failed(
        pg_engine,
        task_id=task.id,
        error="permanent",
        lease_owner=task.lease_owner,
        lease_token=task.lease_token,
    )
    assert applied is True


# mark_failed дважды подряд — второй вернёт False.
@pytest.mark.asyncio
async def test_mark_failed_twice_second_returns_false(pg_engine, clean_task_queue) -> None:
    task = await _create_running_task(pg_engine)
    fence = {"lease_owner": task.lease_owner, "lease_token": task.lease_token}
    first = await mark_failed(pg_engine, task_id=task.id, error="err-1", **fence)
    second = await mark_failed(pg_engine, task_id=task.id, error="err-2", **fence)
    assert first is True
    assert second is False


# mark_failed на status='succeeded' → False, не downgrade'ит.
@pytest.mark.asyncio
async def test_mark_failed_on_succeeded_returns_false(pg_engine, clean_task_queue) -> None:
    task = await _create_running_task(pg_engine)
    fence = {"lease_owner": task.lease_owner, "lease_token": task.lease_token}
    await mark_succeeded(pg_engine, task_id=task.id, **fence)

    applied = await mark_failed(pg_engine, task_id=task.id, error="late fail", **fence)
    assert applied is False

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status FROM task_queue WHERE id = :i"),
                {"i": task.id},
            )
        ).first()
    assert row[0] == "succeeded"


# mark_succeeded на pending (без claim) → False (status='pending', не 'running').
@pytest.mark.asyncio
async def test_mark_succeeded_on_pending_returns_false(pg_engine, clean_task_queue) -> None:
    task_id = await create_task(
        pg_engine,
        task_type="observer_scan",
        idempotency_key=f"pending-{uuid.uuid4().hex[:8]}",
        payload={"source": "test"},
        requested_by="test",
    )
    assert task_id is not None

    # Намеренно НЕ делаем claim — task остаётся в pending.
    applied = await mark_succeeded(
        pg_engine,
        task_id=task_id,
        lease_owner=uuid.uuid4(),
        lease_token=1,
    )
    assert applied is False

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row[0] == "pending"

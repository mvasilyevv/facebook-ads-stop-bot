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

from core.tasks import claim_next_task, create_task, mark_failed, mark_succeeded


@pytest_asyncio.fixture
async def clean_task_queue(pg_engine):
    """Чистка task_queue до и после теста."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue"))

    await _truncate()
    yield
    await _truncate()


async def _create_running_task(pg_engine, *, task_type: str = "disable") -> int:
    """Хелпер: create + claim → task в status='running' и возвращаем id."""
    task_id = await create_task(
        pg_engine,
        task_type=task_type,
        idempotency_key=f"bool-{uuid.uuid4().hex[:8]}",
        payload={"fb_ad_id": "1"},
        requested_by="test",
    )
    assert task_id is not None
    claim = await claim_next_task(pg_engine, task_type=task_type)
    assert claim.task is not None
    return task_id


# mark_succeeded на status='running' → True (нормальный happy-path).
@pytest.mark.asyncio
async def test_mark_succeeded_on_running_returns_true(pg_engine, clean_task_queue) -> None:
    task_id = await _create_running_task(pg_engine)
    applied = await mark_succeeded(pg_engine, task_id=task_id, result={"ok": True})
    assert applied is True


# mark_succeeded дважды подряд — второй вызов вернёт False (status уже 'succeeded').
@pytest.mark.asyncio
async def test_mark_succeeded_twice_second_returns_false(pg_engine, clean_task_queue) -> None:
    task_id = await _create_running_task(pg_engine)
    first = await mark_succeeded(pg_engine, task_id=task_id)
    second = await mark_succeeded(pg_engine, task_id=task_id)
    assert first is True
    assert second is False


# mark_succeeded на status='failed' → False, статус не меняется.
@pytest.mark.asyncio
async def test_mark_succeeded_on_failed_returns_false(pg_engine, clean_task_queue) -> None:
    task_id = await _create_running_task(pg_engine)
    await mark_failed(pg_engine, task_id=task_id, error="boom")

    applied = await mark_succeeded(pg_engine, task_id=task_id, result={"ok": True})
    assert applied is False

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row[0] == "failed"


# mark_failed на status='running' → True (нормальный happy-path).
@pytest.mark.asyncio
async def test_mark_failed_on_running_returns_true(pg_engine, clean_task_queue) -> None:
    task_id = await _create_running_task(pg_engine)
    applied = await mark_failed(pg_engine, task_id=task_id, error="permanent")
    assert applied is True


# mark_failed дважды подряд — второй вернёт False.
@pytest.mark.asyncio
async def test_mark_failed_twice_second_returns_false(pg_engine, clean_task_queue) -> None:
    task_id = await _create_running_task(pg_engine)
    first = await mark_failed(pg_engine, task_id=task_id, error="err-1")
    second = await mark_failed(pg_engine, task_id=task_id, error="err-2")
    assert first is True
    assert second is False


# mark_failed на status='succeeded' → False, не downgrade'ит.
@pytest.mark.asyncio
async def test_mark_failed_on_succeeded_returns_false(pg_engine, clean_task_queue) -> None:
    task_id = await _create_running_task(pg_engine)
    await mark_succeeded(pg_engine, task_id=task_id)

    applied = await mark_failed(pg_engine, task_id=task_id, error="late fail")
    assert applied is False

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row[0] == "succeeded"


# mark_succeeded на pending (без claim) → False (status='pending', не 'running').
@pytest.mark.asyncio
async def test_mark_succeeded_on_pending_returns_false(pg_engine, clean_task_queue) -> None:
    task_id = await create_task(
        pg_engine,
        task_type="disable",
        idempotency_key=f"pending-{uuid.uuid4().hex[:8]}",
        payload={"fb_ad_id": "1"},
        requested_by="test",
    )
    assert task_id is not None

    # Намеренно НЕ делаем claim — task остаётся в pending.
    applied = await mark_succeeded(pg_engine, task_id=task_id)
    assert applied is False

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row[0] == "pending"

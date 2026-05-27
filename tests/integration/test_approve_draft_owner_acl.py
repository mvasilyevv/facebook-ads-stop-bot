# -*- coding: utf-8 -*-
"""Integration: owner ACL у approve_draft_task (CRIT #6).

Покрываем сценарии:
- Approve с правильным chat_id → True, status='pending'.
- Approve с чужим chat_id → False, status остаётся 'draft'.
- Approve draft без chat_id (NULL) + admin_override=True → True.
- Approve draft без chat_id + не-admin (без override) → False.

Тестам нужен реальный Postgres с применённой миграцией
``0002_task_queue_created_by_chat_id``. Если колонки нет — skip.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.meta_api.queue import approve_draft_task
from core.meta_api.schemas import MetaMutationPayload
from core.tasks.queue import create_task


@pytest_asyncio.fixture
async def clean_meta_tasks(pg_engine: AsyncEngine):
    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue WHERE task_type = 'meta_api_mutation'"))

    await _truncate()
    yield
    await _truncate()


@pytest_asyncio.fixture
async def _require_column(pg_engine: AsyncEngine):
    """Skip-фикстура: убеждаемся что миграция 0002 применена."""
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'task_queue'
                      AND column_name = 'created_by_chat_id'
                    """
                )
            )
        ).first()
    if row is None:
        pytest.skip("Колонка task_queue.created_by_chat_id отсутствует — применить миграцию 0002")


def _draft_payload() -> MetaMutationPayload:
    return MetaMutationPayload(
        mutation_kind="set_adset_budget",
        target_id="123",
        params={"daily_budget": 1000},
    )


async def _insert_draft(engine: AsyncEngine, *, requested_by: str, chat_id: int | None) -> int:
    """INSERT draft со status='draft' и заданным created_by_chat_id."""
    task_id = await create_task(
        engine,
        task_type="meta_api_mutation",
        idempotency_key=f"test:{requested_by}:{chat_id}",
        payload=_draft_payload().to_dict(),
        requested_by=requested_by,
        status="draft",
        created_by_chat_id=chat_id,
    )
    assert task_id is not None
    return task_id


# Approve свой draft через совпадение chat_id → DRAFT → PENDING.
@pytest.mark.asyncio
async def test_approve_with_matching_chat_id_succeeds(
    pg_engine: AsyncEngine,
    _require_column,
    clean_meta_tasks,
) -> None:
    task_id = await _insert_draft(pg_engine, requested_by="tg:alice", chat_id=11111)
    ok = await approve_draft_task(
        pg_engine,
        task_id=task_id,
        approved_by="tg:alice",
        approver_chat_id=11111,
    )
    assert ok is True
    async with pg_engine.connect() as conn:
        status = (
            await conn.execute(text("SELECT status FROM task_queue WHERE id=:i"), {"i": task_id})
        ).scalar()
    assert status == "pending"


# Approve чужой draft → False, status остаётся 'draft' (защита от bruteforce).
@pytest.mark.asyncio
async def test_approve_with_wrong_chat_id_blocked(
    pg_engine: AsyncEngine,
    _require_column,
    clean_meta_tasks,
) -> None:
    task_id = await _insert_draft(pg_engine, requested_by="tg:alice", chat_id=11111)
    ok = await approve_draft_task(
        pg_engine,
        task_id=task_id,
        approved_by="tg:mallory",
        approver_chat_id=99999,  # чужой chat_id
    )
    assert ok is False
    async with pg_engine.connect() as conn:
        status = (
            await conn.execute(text("SELECT status FROM task_queue WHERE id=:i"), {"i": task_id})
        ).scalar()
    assert status == "draft"


# MCP-draft (created_by_chat_id IS NULL) + admin_override=True → approve проходит.
@pytest.mark.asyncio
async def test_approve_null_chat_with_admin_override(
    pg_engine: AsyncEngine,
    _require_column,
    clean_meta_tasks,
) -> None:
    task_id = await _insert_draft(pg_engine, requested_by="mcp:claude", chat_id=None)
    ok = await approve_draft_task(
        pg_engine,
        task_id=task_id,
        approved_by="tg:admin",
        admin_override=True,
    )
    assert ok is True


# MCP-draft без admin_override и без chat_id → False (нельзя approve безхозный).
@pytest.mark.asyncio
async def test_approve_null_chat_without_override_blocked(
    pg_engine: AsyncEngine,
    _require_column,
    clean_meta_tasks,
) -> None:
    task_id = await _insert_draft(pg_engine, requested_by="mcp:claude", chat_id=None)
    ok = await approve_draft_task(
        pg_engine,
        task_id=task_id,
        approved_by="tg:any-user",
        approver_chat_id=12345,
    )
    assert ok is False
    async with pg_engine.connect() as conn:
        status = (
            await conn.execute(text("SELECT status FROM task_queue WHERE id=:i"), {"i": task_id})
        ).scalar()
    assert status == "draft"


# Approver вообще без chat_id и без override → False, никаких изменений.
@pytest.mark.asyncio
async def test_approve_without_chat_id_or_override_returns_false(
    pg_engine: AsyncEngine,
    _require_column,
    clean_meta_tasks,
) -> None:
    task_id = await _insert_draft(pg_engine, requested_by="tg:alice", chat_id=42)
    ok = await approve_draft_task(
        pg_engine,
        task_id=task_id,
        approved_by="tg:noone",
        # ни approver_chat_id, ни admin_override
    )
    assert ok is False

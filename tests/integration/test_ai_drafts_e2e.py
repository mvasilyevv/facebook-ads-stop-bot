# -*- coding: utf-8 -*-
"""Integration: DRAFT tool → task_queue → approve / cancel.

Покрывает связку:
- DRAFT tool создаёт запись в task_queue (task_type='meta_api_mutation', status='draft').
- approve_draft_task переводит DRAFT → PENDING.
- cancel_task переводит DRAFT/PENDING → CANCELLED.

Требует реальный Postgres (pg_engine fixture из tests/integration/conftest.py).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.ai_assistant.tools.base import ToolContext
from core.ai_assistant.tools.drafts.request_budget_change import RequestBudgetChangeTool
from core.ai_assistant.tools.drafts.request_bulk_pause import RequestBulkPauseTool
from core.meta_api.queue import approve_draft_task, cancel_task


@pytest_asyncio.fixture
async def clean_meta_tasks(pg_engine: AsyncEngine):
    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue WHERE task_type = 'meta_api_mutation'"))

    await _truncate()
    yield
    await _truncate()


def _ctx(pg_engine: AsyncEngine) -> ToolContext:
    return ToolContext(client_key="user-int", engine=pg_engine, requested_by="tg:integration")


# DRAFT-tool вставляет строку в task_queue; статус draft; payload содержит mutation_kind.
@pytest.mark.asyncio
async def test_budget_change_creates_draft_row(
    pg_engine: AsyncEngine,
    clean_meta_tasks,
) -> None:
    tool = RequestBudgetChangeTool()
    result = await tool.run(
        _ctx(pg_engine),
        {
            "adset_id": "23000111",
            "ad_account_id": "act_42",
            "daily_budget_usd": 12.34,
            "reason": "e2e test",
        },
    )
    assert "task_id=" in result

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT id, task_type, status, payload, requested_by
                    FROM task_queue
                    WHERE task_type = 'meta_api_mutation'
                    ORDER BY id DESC LIMIT 1
                    """
                )
            )
        ).first()
    assert row is not None
    assert row[1] == "meta_api_mutation"
    assert row[2] == "draft"
    payload = row[3]
    assert payload["mutation_kind"] == "set_adset_budget"
    assert payload["target_id"] == "23000111"
    assert payload["params"]["daily_budget"] == 1234
    assert row[4] == "tg:integration"


# DRAFT → PENDING через approve_draft_task; повторный approve = no-op (False).
@pytest.mark.asyncio
async def test_approve_draft_transitions_to_pending(
    pg_engine: AsyncEngine,
    clean_meta_tasks,
) -> None:
    tool = RequestBulkPauseTool()
    await tool.run(
        _ctx(pg_engine),
        {"ad_ids": ["55501", "55502"]},
    )

    async with pg_engine.connect() as conn:
        task_id = (
            await conn.execute(
                text(
                    "SELECT id FROM task_queue "
                    "WHERE task_type = 'meta_api_mutation' "
                    "ORDER BY id DESC LIMIT 1"
                )
            )
        ).scalar()
    assert task_id is not None

    ok = await approve_draft_task(pg_engine, task_id=task_id, approved_by="tg:operator")
    assert ok is True

    again = await approve_draft_task(pg_engine, task_id=task_id, approved_by="tg:operator")
    assert again is False

    async with pg_engine.connect() as conn:
        status = (
            await conn.execute(text("SELECT status FROM task_queue WHERE id = :i"), {"i": task_id})
        ).scalar()
    assert status == "pending"


# cancel_task переводит DRAFT → cancelled; повторно — no-op.
@pytest.mark.asyncio
async def test_cancel_draft_transitions_to_cancelled(
    pg_engine: AsyncEngine,
    clean_meta_tasks,
) -> None:
    tool = RequestBudgetChangeTool()
    await tool.run(
        _ctx(pg_engine),
        {"adset_id": "23000222", "lifetime_budget_usd": 100},
    )

    async with pg_engine.connect() as conn:
        task_id = (
            await conn.execute(
                text(
                    "SELECT id FROM task_queue "
                    "WHERE task_type = 'meta_api_mutation' "
                    "ORDER BY id DESC LIMIT 1"
                )
            )
        ).scalar()
    assert task_id is not None

    cancelled = await cancel_task(pg_engine, task_id=task_id, reason="передумал")
    assert cancelled is True
    again = await cancel_task(pg_engine, task_id=task_id, reason="ещё раз")
    assert again is False

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, last_error FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row[0] == "cancelled"
    assert "передумал" in (row[1] or "")

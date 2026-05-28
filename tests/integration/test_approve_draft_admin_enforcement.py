# -*- coding: utf-8 -*-
"""Integration: approve_draft_task admin_override требует верифицированного owner.

CRIT #2 из backend_test_audit_round_8: admin_override=True с approver_chat_id
ранее не проверял роль approver'а внутри функции — caller обязан был вызвать
is_admin_recipient самостоятельно. Это потенциальный security gap при ошибке
caller'а. Фикс добавил is_admin_recipient проверку внутри approve_draft_task.

Проверяем 4 сценария:
1. admin_override=True + owner chat_id → approve проходит.
2. admin_override=True + non-owner chat_id → PermissionError.
3. admin_override=True + approver_chat_id=None → MCP-path (только для NULL draft).
4. Backward compat: без admin_override — обычная owner ACL по chat_id.
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
async def clean_admin_tasks(pg_engine: AsyncEngine):
    """Чистим task_queue + recipients между тестами."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue WHERE task_type = 'meta_api_mutation'"))
            await conn.execute(text("DELETE FROM telegram_recipients"))

    await _truncate()
    yield
    await _truncate()


def _draft_payload() -> dict:
    return MetaMutationPayload(
        mutation_kind="pause_ad",
        target_id="9991",
        params={},
    ).to_dict()


async def _mk_draft(engine: AsyncEngine, *, chat_id: int | None, suffix: str = "") -> int:
    """Вспомогательная функция: создаёт draft в task_queue."""
    task_id = await create_task(
        engine,
        task_type="meta_api_mutation",
        idempotency_key=f"admin-test:{suffix}:{chat_id}",
        payload=_draft_payload(),
        requested_by=f"tg:user-{suffix}",
        status="draft",
        created_by_chat_id=chat_id,
    )
    assert task_id is not None
    return task_id


async def _add_owner_recipient(engine: AsyncEngine, *, chat_id: int) -> None:
    """Добавляем recipient с role='owner' для тестирования is_admin_recipient."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_recipients
                    (chat_id, telegram_user_id, username, role)
                VALUES (:cid, :uid, :un, 'owner')
                """
            ),
            {"cid": chat_id, "uid": chat_id + 1000, "un": f"owner_{chat_id}"},
        )


async def _add_plain_recipient(engine: AsyncEngine, *, chat_id: int) -> None:
    """Добавляем recipient с role='recipient' (не admin)."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_recipients
                    (chat_id, telegram_user_id, username, role)
                VALUES (:cid, :uid, :un, 'recipient')
                """
            ),
            {"cid": chat_id, "uid": chat_id + 2000, "un": f"user_{chat_id}"},
        )


# admin_override=True + owner chat_id → approve должен пройти.
@pytest.mark.asyncio
async def test_admin_override_with_verified_owner_approves(
    pg_engine: AsyncEngine,
    clean_admin_tasks,
) -> None:
    """Верифицированный owner (role='owner') с admin_override=True может approve."""
    owner_chat_id = 77701
    await _add_owner_recipient(pg_engine, chat_id=owner_chat_id)
    task_id = await _mk_draft(pg_engine, chat_id=22222, suffix="owner-ok")

    ok = await approve_draft_task(
        pg_engine,
        task_id=task_id,
        approved_by="tg:owner",
        approver_chat_id=owner_chat_id,
        admin_override=True,
    )
    assert ok is True

    async with pg_engine.connect() as conn:
        status = (
            await conn.execute(text("SELECT status FROM task_queue WHERE id = :i"), {"i": task_id})
        ).scalar()
    assert status == "pending"


# admin_override=True + non-owner chat_id → PermissionError.
@pytest.mark.asyncio
async def test_admin_override_with_non_owner_raises_permission_error(
    pg_engine: AsyncEngine,
    clean_admin_tasks,
) -> None:
    """Обычный recipient (role='recipient') не может использовать admin_override=True."""
    plain_chat_id = 77702
    await _add_plain_recipient(pg_engine, chat_id=plain_chat_id)
    task_id = await _mk_draft(pg_engine, chat_id=33333, suffix="non-owner-fail")

    with pytest.raises(PermissionError, match="admin_override требует role='owner'"):
        await approve_draft_task(
            pg_engine,
            task_id=task_id,
            approved_by="tg:not-admin",
            approver_chat_id=plain_chat_id,
            admin_override=True,
        )

    # Draft статус не изменился
    async with pg_engine.connect() as conn:
        status = (
            await conn.execute(text("SELECT status FROM task_queue WHERE id = :i"), {"i": task_id})
        ).scalar()
    assert status == "draft"


# admin_override=True + approver_chat_id=None → MCP-path для NULL-draft.
@pytest.mark.asyncio
async def test_admin_override_without_chat_id_approves_mcp_draft(
    pg_engine: AsyncEngine,
    clean_admin_tasks,
) -> None:
    """MCP-путь: admin_override + нет chat_id → approve draft с NULL created_by_chat_id."""
    task_id = await _mk_draft(pg_engine, chat_id=None, suffix="mcp-null")

    ok = await approve_draft_task(
        pg_engine,
        task_id=task_id,
        approved_by="mcp:claude",
        approver_chat_id=None,
        admin_override=True,
    )
    assert ok is True

    async with pg_engine.connect() as conn:
        status = (
            await conn.execute(text("SELECT status FROM task_queue WHERE id = :i"), {"i": task_id})
        ).scalar()
    assert status == "pending"


# Backward compat: без admin_override — owner check по совпадению chat_id.
@pytest.mark.asyncio
async def test_normal_owner_chat_id_match_still_works(
    pg_engine: AsyncEngine,
    clean_admin_tasks,
) -> None:
    """Обратная совместимость: approver_chat_id совпадает с created_by_chat_id → approve."""
    task_id = await _mk_draft(pg_engine, chat_id=55555, suffix="compat")

    ok = await approve_draft_task(
        pg_engine,
        task_id=task_id,
        approved_by="tg:owner-compat",
        approver_chat_id=55555,
        # admin_override НЕ передаётся
    )
    assert ok is True

    async with pg_engine.connect() as conn:
        status = (
            await conn.execute(text("SELECT status FROM task_queue WHERE id = :i"), {"i": task_id})
        ).scalar()
    assert status == "pending"

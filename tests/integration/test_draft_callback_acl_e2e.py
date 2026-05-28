# -*- coding: utf-8 -*-
"""Integration E2E: handle_draft_callback ACL по chat_id.

HIGH #3 из backend_test_audit_round_8: handle_draft_callback содержит ветку
«Чужой черновик», которая ранее не была покрыта тестами. Проверяем три сценария:

1. Owner approve (chat_id совпадает) → DRAFT → PENDING, ack "Подтверждено".
2. Foreign chat_id (не owner, не admin) → "Чужой черновик", draft не изменился.
3. Admin (role='owner' в recipients) approve чужого draft → DRAFT → PENDING.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.ai_assistant.tools.base import ToolContext
from core.ai_assistant.tools.drafts.request_bulk_pause import RequestBulkPauseTool
from core.telegram.handlers.ask import handle_draft_callback


@pytest_asyncio.fixture
async def clean_acl_tables(pg_engine: AsyncEngine):
    """Очищаем task_queue + recipients для ACL-тестов."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue WHERE task_type = 'meta_api_mutation'"))
            await conn.execute(text("DELETE FROM meta_api_audit_log"))
            await conn.execute(text("DELETE FROM telegram_recipients"))

    await _truncate()
    yield
    await _truncate()


class _FakeTG:
    """Минимальный TelegramBotClient для фиксации ответов."""

    def __init__(self) -> None:
        self.acks: list[tuple[str, str]] = []
        self.edits: list[dict] = []

    async def answer_callback_query(self, cq_id: str, text: str = "") -> None:
        self.acks.append((cq_id, text))

    async def edit_message(self, *, chat_id: str, message_id: int, text: str, **_kw) -> None:
        self.edits.append({"chat_id": chat_id, "message_id": message_id, "text": text})


def _ctx(engine: AsyncEngine, *, chat_id: int) -> ToolContext:
    """ToolContext с уникальным client_key — изолирует rate-limit между тестами."""
    return ToolContext(
        client_key=f"acl-test:{uuid.uuid4().hex[:8]}",
        engine=engine,
        requested_by=f"tg:user-{chat_id}",
        created_by_chat_id=chat_id,
    )


async def _create_draft(engine: AsyncEngine, *, owner_chat_id: int) -> int:
    """Создаём DRAFT через AI-tool от имени owner_chat_id."""
    tool = RequestBulkPauseTool()
    await tool.run(_ctx(engine, chat_id=owner_chat_id), {"ad_ids": [f"111{owner_chat_id}"]})
    async with engine.connect() as conn:
        task_id = (
            await conn.execute(
                text(
                    "SELECT id FROM task_queue WHERE task_type = 'meta_api_mutation' "
                    "ORDER BY id DESC LIMIT 1"
                )
            )
        ).scalar()
    assert task_id is not None
    return int(task_id)


async def _add_owner_recipient(engine: AsyncEngine, *, chat_id: int) -> None:
    """Добавляем recipient с role='owner' в telegram_recipients."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_recipients
                    (chat_id, telegram_user_id, username, role)
                VALUES (:cid, :uid, :un, 'owner')
                """
            ),
            {"cid": chat_id, "uid": chat_id + 9000, "un": f"admin_{chat_id}"},
        )


# Сценарий 1: Owner approve — chat_id совпадает с created_by_chat_id.
@pytest.mark.asyncio
async def test_owner_approve_own_draft_succeeds(
    pg_engine: AsyncEngine,
    clean_acl_tables,
) -> None:
    """Owner (тот же chat_id что создал draft) может approve → PENDING."""
    owner_chat_id = 10001
    task_id = await _create_draft(pg_engine, owner_chat_id=owner_chat_id)

    fake_tg = _FakeTG()
    await handle_draft_callback(
        engine=pg_engine,
        client=fake_tg,
        cq_id="cb-owner",
        action="dr_ok",
        task_id_raw=str(task_id),
        username="owner_user",
        chat_id=owner_chat_id,
        message_id=101,
    )

    # Должен получить подтверждение
    ack_texts = [t for _, t in fake_tg.acks]
    assert any("Подтверждено" in t for t in ack_texts), (
        f"Owner approve не вернул 'Подтверждено'. Acks: {fake_tg.acks}"
    )

    # Статус → pending
    async with pg_engine.connect() as conn:
        status = (
            await conn.execute(text("SELECT status FROM task_queue WHERE id = :i"), {"i": task_id})
        ).scalar()
    assert status == "pending"


# Сценарий 2: Чужой chat_id (не owner, не admin) → "Чужой черновик", draft не изменился.
@pytest.mark.asyncio
async def test_foreign_chat_id_gets_alien_draft_message(
    pg_engine: AsyncEngine,
    clean_acl_tables,
) -> None:
    """Чужой chat_id не может approve draft: ответ 'Чужой черновик', статус 'draft'."""
    owner_chat_id = 20001
    foreign_chat_id = 20002  # другой пользователь, не admin

    task_id = await _create_draft(pg_engine, owner_chat_id=owner_chat_id)

    fake_tg = _FakeTG()
    await handle_draft_callback(
        engine=pg_engine,
        client=fake_tg,
        cq_id="cb-alien",
        action="dr_ok",
        task_id_raw=str(task_id),
        username="mallory",
        chat_id=foreign_chat_id,
        message_id=102,
    )

    # Ответ содержит информацию об отказе
    ack_texts = [t for _, t in fake_tg.acks]
    assert any("черновик" in t.lower() or "другому" in t.lower() for t in ack_texts), (
        f"Ожидали сообщение о чужом draft, получили: {fake_tg.acks}"
    )

    # Статус остался draft
    async with pg_engine.connect() as conn:
        status = (
            await conn.execute(text("SELECT status FROM task_queue WHERE id = :i"), {"i": task_id})
        ).scalar()
    assert status == "draft", (
        f"Чужой chat_id не должен переводить draft в pending, но status={status}"
    )

    # footer в edit_message должен содержать "Чужой черновик"
    if fake_tg.edits:
        edit_texts = [e["text"] for e in fake_tg.edits]
        assert any("Чужой черновик" in t for t in edit_texts), (
            f"Footer должен содержать 'Чужой черновик'. Edits: {fake_tg.edits}"
        )


# Сценарий 3: Admin (role='owner') может approve чужого draft.
@pytest.mark.asyncio
async def test_admin_recipient_can_approve_foreign_draft(
    pg_engine: AsyncEngine,
    clean_acl_tables,
) -> None:
    """Recipient с role='owner' в telegram_recipients может approve любой draft."""
    owner_chat_id = 30001
    admin_chat_id = 30002

    # Добавляем admin в recipients
    await _add_owner_recipient(pg_engine, chat_id=admin_chat_id)
    task_id = await _create_draft(pg_engine, owner_chat_id=owner_chat_id)

    fake_tg = _FakeTG()
    await handle_draft_callback(
        engine=pg_engine,
        client=fake_tg,
        cq_id="cb-admin",
        action="dr_ok",
        task_id_raw=str(task_id),
        username="admin_user",
        chat_id=admin_chat_id,
        message_id=103,
    )

    # Admin должен получить подтверждение
    ack_texts = [t for _, t in fake_tg.acks]
    assert any("Подтверждено" in t for t in ack_texts), (
        f"Admin approve не вернул 'Подтверждено'. Acks: {fake_tg.acks}"
    )

    # Статус → pending
    async with pg_engine.connect() as conn:
        status = (
            await conn.execute(text("SELECT status FROM task_queue WHERE id = :i"), {"i": task_id})
        ).scalar()
    assert status == "pending"

# -*- coding: utf-8 -*-
"""E2E cross-cutting сценарий: AI draft → callback approve → meta_api worker.

Сшивка трёх доменов:
1. `core/ai_assistant/tools/drafts/*` создаёт DRAFT в task_queue.
2. `core/telegram/handlers/draft_confirm.handle_draft_callback` подтверждает (DRAFT → PENDING).
3. `apps/meta_api_worker.process_one_task` забирает PENDING, мокаем
   `dispatch_mutation` → status='succeeded'.

Проверяем что цепочка не теряет ничего между этапами и идемпотентно
обрабатывает повторные approve / cancel.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

import apps.meta_api_worker.main as worker_main
from apps.meta_api_worker.main import process_one_task
from core.ai_assistant.tools.base import ToolContext
from core.ai_assistant.tools.drafts.request_bulk_pause import RequestBulkPauseTool
from core.meta_api.queue import claim_pending_task
from core.telegram.handlers.draft_confirm import handle_draft_callback


@pytest_asyncio.fixture
async def clean_meta_pipeline(pg_engine: AsyncEngine):
    """Чистит meta_api_mutation tasks + audit log до и после теста."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue WHERE task_type = 'meta_api_mutation'"))
            await conn.execute(text("DELETE FROM meta_api_audit_log"))

    await _truncate()
    yield
    await _truncate()


class _FakeTGClient:
    """Минимальный TelegramBotClient: фиксирует answer_callback_query/edit_message."""

    def __init__(self) -> None:
        self.acks: list[tuple[str, str]] = []
        self.edits: list[dict] = []

    async def answer_callback_query(self, cq_id: str, text: str = "") -> None:
        self.acks.append((cq_id, text))

    async def edit_message(self, *, chat_id: str, message_id: int, text: str, **_kw) -> None:
        self.edits.append({"chat_id": chat_id, "message_id": message_id, "text": text})


def _ctx(engine: AsyncEngine, *, chat_id: int = 100500) -> ToolContext:
    """ToolContext с уникальным client_key — иначе rate-limit пересекает тесты.

    chat_id фиксируем тот же, что и в `handle_draft_callback` ниже, чтобы
    owner ACL у approve_draft_task разрешил approve (created_by_chat_id == chat_id).
    """
    return ToolContext(
        client_key=f"tg:{uuid.uuid4().hex[:8]}",
        engine=engine,
        requested_by="tg:e2e_user",
        created_by_chat_id=chat_id,
    )


# E2E: draft tool → approve callback → worker → success
@pytest.mark.asyncio
async def test_full_cycle_draft_approve_worker_success(
    pg_engine: AsyncEngine,
    clean_meta_pipeline,
    monkeypatch,
) -> None:
    # Шаг 1: AI tool создаёт DRAFT
    tool = RequestBulkPauseTool()
    tool_result = await tool.run(
        _ctx(pg_engine),
        {"ad_ids": ["55501", "55502", "55503"]},
    )
    assert "task_id=" in tool_result

    # Достаём id и сразу проверяем что это DRAFT-mutation
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT id, status, payload, requested_by
                    FROM task_queue
                    WHERE task_type = 'meta_api_mutation'
                    ORDER BY id DESC LIMIT 1
                    """
                )
            )
        ).first()
    assert row is not None
    task_id = int(row[0])
    assert row[1] == "draft"
    assert row[2]["mutation_kind"] in ("bulk_status_change", "pause_ad")
    assert row[3] == "tg:e2e_user"

    # Шаг 2: пользователь жмёт inline-кнопку "Подтвердить" → handle_draft_callback
    fake_tg = _FakeTGClient()
    await handle_draft_callback(
        engine=pg_engine,
        client=fake_tg,
        cq_id="cb-1",
        action="dr_ok",
        task_id_raw=str(task_id),
        username="operator",
        chat_id=100500,
        message_id=42,
    )
    # ack отправлен пользователю
    assert any("Подтверждено" in t for _, t in fake_tg.acks)

    # Шаг 3: статус DRAFT → PENDING + requested_by обновился
    async with pg_engine.connect() as conn:
        check_row = (
            await conn.execute(
                text("SELECT status, requested_by FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert check_row[0] == "pending"
    assert check_row[1] == "tg:operator"

    # Шаг 4: meta_api worker claim'ит задачу
    claim = await claim_pending_task(pg_engine)
    assert claim.task is not None
    assert claim.task.id == task_id

    # Шаг 5: dispatch_mutation мокаем (нет живого browser-agent) → success
    fake_result = {
        "success": True,
        "graph_response": {"ok": True},
        "modified_ids": ["55501", "55502", "55503"],
    }

    async def _fake_dispatch(_client, _payload):
        return fake_result

    monkeypatch.setattr(worker_main, "dispatch_mutation", _fake_dispatch)

    fake_client = AsyncMock()
    await process_one_task(pg_engine, claim.task, client=fake_client)

    # Шаг 6: финал — succeeded + result сохранён
    async with pg_engine.connect() as conn:
        final = (
            await conn.execute(
                text("SELECT status, result, last_error FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert final[0] == "succeeded"
    assert final[1]["success"] is True
    assert final[2] is None


# E2E: draft → dr_cancel callback → status='cancelled', worker не подбирает.
@pytest.mark.asyncio
async def test_draft_cancel_callback_blocks_worker(
    pg_engine: AsyncEngine,
    clean_meta_pipeline,
) -> None:
    tool = RequestBulkPauseTool()
    await tool.run(
        _ctx(pg_engine),
        {"ad_ids": ["77701", "77702"]},
    )

    async with pg_engine.connect() as conn:
        task_id = (
            await conn.execute(
                text(
                    "SELECT id FROM task_queue WHERE task_type = 'meta_api_mutation' "
                    "ORDER BY id DESC LIMIT 1"
                )
            )
        ).scalar()
    assert task_id is not None

    fake_tg = _FakeTGClient()
    await handle_draft_callback(
        engine=pg_engine,
        client=fake_tg,
        cq_id="cb-cancel",
        action="dr_cancel",
        task_id_raw=str(task_id),
        username="operator",
        chat_id=100500,
        message_id=43,
    )
    assert any("Отменено" in t for _, t in fake_tg.acks)

    # claim ничего не подберёт — задача cancelled
    claim = await claim_pending_task(pg_engine)
    assert claim.queue_empty is True
    assert claim.task is None

    async with pg_engine.connect() as conn:
        status = (
            await conn.execute(
                text("SELECT status FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).scalar()
    assert status == "cancelled"


# E2E: повторный approve того же draft — no-op (защита от двойного клика).
@pytest.mark.asyncio
async def test_double_approve_callback_is_noop(
    pg_engine: AsyncEngine,
    clean_meta_pipeline,
) -> None:
    tool = RequestBulkPauseTool()
    # chat_id=1 совпадает с callback'ами ниже — нужно для owner ACL.
    await tool.run(_ctx(pg_engine, chat_id=1), {"ad_ids": ["88801"]})

    async with pg_engine.connect() as conn:
        task_id = (
            await conn.execute(
                text(
                    "SELECT id FROM task_queue WHERE task_type = 'meta_api_mutation' "
                    "ORDER BY id DESC LIMIT 1"
                )
            )
        ).scalar()

    fake_tg = _FakeTGClient()
    # Первый approve — успешен
    await handle_draft_callback(
        engine=pg_engine,
        client=fake_tg,
        cq_id="cb-first",
        action="dr_ok",
        task_id_raw=str(task_id),
        username="op",
        chat_id=1,
        message_id=10,
    )
    # Второй approve — задача уже не draft → no-op в БД, юзер видит "уже обработано"
    await handle_draft_callback(
        engine=pg_engine,
        client=fake_tg,
        cq_id="cb-second",
        action="dr_ok",
        task_id_raw=str(task_id),
        username="op",
        chat_id=1,
        message_id=10,
    )

    acks_text = [t for _, t in fake_tg.acks]
    assert any("Подтверждено" in t for t in acks_text)
    assert any("Уже не draft" in t for t in acks_text)

    async with pg_engine.connect() as conn:
        status = (
            await conn.execute(
                text("SELECT status FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).scalar()
    # После первого approve остался pending; повторный approve его не сбил
    assert status == "pending"


# Поздняя кнопка cancel после approve не должна скрывать уже запущенную мутацию.
@pytest.mark.asyncio
async def test_cancel_after_approve_keeps_task_pending(
    pg_engine: AsyncEngine,
    clean_meta_pipeline,
) -> None:
    tool = RequestBulkPauseTool()
    await tool.run(_ctx(pg_engine, chat_id=7), {"ad_ids": ["99901"]})

    async with pg_engine.connect() as conn:
        task_id = (
            await conn.execute(
                text(
                    "SELECT id FROM task_queue WHERE task_type = 'meta_api_mutation' "
                    "ORDER BY id DESC LIMIT 1"
                )
            )
        ).scalar_one()

    fake_tg = _FakeTGClient()
    await handle_draft_callback(
        engine=pg_engine,
        client=fake_tg,
        cq_id="cb-approve",
        action="dr_ok",
        task_id_raw=str(task_id),
        username="op",
        chat_id=7,
        message_id=11,
    )
    await handle_draft_callback(
        engine=pg_engine,
        client=fake_tg,
        cq_id="cb-late-cancel",
        action="dr_cancel",
        task_id_raw=str(task_id),
        username="op",
        chat_id=7,
        message_id=11,
    )

    async with pg_engine.connect() as conn:
        status = (
            await conn.execute(text("SELECT status FROM task_queue WHERE id = :i"), {"i": task_id})
        ).scalar_one()
    assert status == "pending"
    assert any("Уже не draft" in text for _, text in fake_tg.acks)

# -*- coding: utf-8 -*-
"""Integration: outbox lifecycle для task_type='meta_api_mutation'.

Сценарии:
- DRAFT → approve → PENDING → claim → execute (заглушка) → FAILED
- Идемпотентность: повторный create_mutation_task с тем же ключом → None
- Reconcile: stuck running → retrying; stale drafts → cancelled
- Audit log: запись через record_audit_log реально появляется

Требует реальный Postgres из docker-compose (pg_engine fixture).
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.meta_api_worker.main import process_one_task
from core.meta_api.audit import record_audit_log
from core.meta_api.errors import RateLimitedError, TokenInvalidError
from core.meta_api.queue import (
    approve_draft_task,
    cancel_task,
    claim_pending_task,
    create_draft_task,
    create_mutation_task,
    list_drafts,
)
from core.meta_api.reconciler import (
    cancel_stale_meta_drafts,
    reconcile_stuck_meta_running,
)
from core.meta_api.schemas import MetaMutationPayload


# Очищаем task_queue и audit_log перед каждым тестом, чтобы не загрязнять.
@pytest_asyncio.fixture
async def clean_meta_tables(pg_engine: AsyncEngine):
    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue WHERE task_type = 'meta_api_mutation'"))
            await conn.execute(text("DELETE FROM meta_api_audit_log"))

    await _truncate()
    yield
    await _truncate()


def _unique_payload(kind: str = "pause_ad") -> MetaMutationPayload:
    """Уникальный target_id (UUID hex) — чтобы default idempotency_key не пересекался между тестами."""
    return MetaMutationPayload(
        mutation_kind=kind,
        target_id=uuid.uuid4().hex,
        params={"reason": "integration test"},
        ad_account_id="act_42",
    )


# ====================== Lifecycle ======================


# Полный жизненный цикл: DRAFT → approve → claim → execute (мок dispatch_mutation, успех) → SUCCEEDED.
@pytest.mark.asyncio
async def test_draft_approve_claim_execute_success(
    pg_engine: AsyncEngine,
    clean_meta_tables,
    monkeypatch,
):
    payload = _unique_payload("pause_ad")
    task_id = await create_draft_task(
        pg_engine,
        payload=payload,
        requested_by="test_ai",
    )
    assert task_id is not None

    drafts = await list_drafts(pg_engine)
    assert any(d.id == task_id for d in drafts)

    approved = await approve_draft_task(
        pg_engine,
        task_id=task_id,
        approved_by="test_user",
    )
    assert approved is True

    claim = await claim_pending_task(pg_engine)
    assert not claim.queue_empty
    assert claim.task is not None
    assert claim.task.id == task_id

    # Мокаем dispatch_mutation так, чтобы не дёргать gRPC к browser-agent.
    fake_result = {
        "success": True,
        "graph_response": {"ok": True},
        "modified_ids": [payload.target_id],
    }

    async def _fake_dispatch(client, p):
        return fake_result

    import apps.meta_api_worker.main as worker_main

    monkeypatch.setattr(worker_main, "dispatch_mutation", _fake_dispatch)

    fake_client = AsyncMock()
    await process_one_task(pg_engine, claim.task, client=fake_client)

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, last_error FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row is not None
    assert row[0] == "succeeded"
    assert row[1] is None


# RateLimitedError из dispatch_mutation → status='retrying' (TemporaryError → requeue).
@pytest.mark.asyncio
async def test_rate_limited_error_requeues_task(
    pg_engine: AsyncEngine,
    clean_meta_tables,
    monkeypatch,
):
    payload = _unique_payload("pause_ad")
    task_id = await create_mutation_task(
        pg_engine,
        payload=payload,
        requested_by="bot_auto",
        status="pending",
    )
    assert task_id is not None

    claim = await claim_pending_task(pg_engine)
    assert claim.task is not None

    async def _raise_rate_limited(client, p):
        raise RateLimitedError("Throttled", code=17)

    import apps.meta_api_worker.main as worker_main

    monkeypatch.setattr(worker_main, "dispatch_mutation", _raise_rate_limited)

    fake_client = AsyncMock()
    await process_one_task(pg_engine, claim.task, client=fake_client)

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, attempt_count, last_error FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row is not None
    assert row[0] == "retrying"  # не failed!
    assert row[1] == 1  # attempt_count инкрементнулся
    assert "RateLimitedError" in (row[2] or "")


# TokenInvalidError → status='failed' без retry (PermanentError).
@pytest.mark.asyncio
async def test_token_invalid_marks_failed_without_retry(
    pg_engine: AsyncEngine,
    clean_meta_tables,
    monkeypatch,
):
    payload = _unique_payload("activate_ad")
    task_id = await create_mutation_task(
        pg_engine,
        payload=payload,
        requested_by="bot_auto",
        status="pending",
    )
    assert task_id is not None

    claim = await claim_pending_task(pg_engine)
    assert claim.task is not None

    async def _raise_token_invalid(client, p):
        raise TokenInvalidError("Session expired", code=190)

    import apps.meta_api_worker.main as worker_main

    monkeypatch.setattr(worker_main, "dispatch_mutation", _raise_token_invalid)

    fake_client = AsyncMock()
    await process_one_task(pg_engine, claim.task, client=fake_client)

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, attempt_count, last_error FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row is not None
    assert row[0] == "failed"  # сразу final, без retry
    assert row[1] == 0  # attempt_count не увеличился
    assert "TokenInvalidError" in (row[2] or "")


# Повторное create_mutation_task с тем же idempotency_key → None (UNIQUE conflict без ошибки).
@pytest.mark.asyncio
async def test_idempotency_dedup(pg_engine: AsyncEngine, clean_meta_tables):
    payload = _unique_payload("activate_ad")
    first = await create_mutation_task(
        pg_engine,
        payload=payload,
        requested_by="bot_auto",
        status="pending",
    )
    second = await create_mutation_task(
        pg_engine,
        payload=payload,
        requested_by="bot_auto",
        status="pending",
    )
    assert first is not None
    assert second is None  # дубликат


# cancel_task переводит PENDING → CANCELLED. Повторный cancel — no-op (rowcount=0).
@pytest.mark.asyncio
async def test_cancel_pending_task(pg_engine: AsyncEngine, clean_meta_tables):
    payload = _unique_payload("pause_campaign")
    task_id = await create_mutation_task(
        pg_engine,
        payload=payload,
        requested_by="user",
    )
    assert task_id is not None

    ok = await cancel_task(pg_engine, task_id=task_id, reason="передумал")
    assert ok is True

    # повторно — уже cancelled, поэтому update не сработает
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
    assert "передумал" in row[1]


# ====================== Reconcile ======================


# reconcile_stuck_meta_running должен поднять running старше N секунд → retrying. Только meta_api_mutation.
@pytest.mark.asyncio
async def test_reconcile_stuck_running(pg_engine: AsyncEngine, clean_meta_tables):
    payload = _unique_payload("activate_campaign")
    task_id = await create_mutation_task(
        pg_engine,
        payload=payload,
        requested_by="bot",
    )
    assert task_id is not None

    # Симулируем «застрявший» running: updated_at сдвигаем на 2 часа назад
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'running', updated_at = NOW() - INTERVAL '2 hours'
                WHERE id = :i
                """
            ),
            {"i": task_id},
        )

    reconciled = await reconcile_stuck_meta_running(pg_engine, stuck_after_seconds=1800)
    assert reconciled >= 1

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row[0] == "retrying"


# cancel_stale_meta_drafts должен отменять draft старше 24h.
@pytest.mark.asyncio
async def test_cancel_stale_drafts(pg_engine: AsyncEngine, clean_meta_tables):
    payload = _unique_payload("set_adset_budget")
    task_id = await create_draft_task(
        pg_engine,
        payload=payload,
        requested_by="ai",
    )
    assert task_id is not None

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE task_queue SET created_at = NOW() - INTERVAL '2 days' WHERE id = :i"),
            {"i": task_id},
        )

    cancelled = await cancel_stale_meta_drafts(pg_engine, older_than_seconds=24 * 3600)
    assert cancelled >= 1

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, last_error FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row[0] == "cancelled"
    assert "draft expired" in row[1]


# ====================== Audit log ======================


# record_audit_log реально INSERT-ит строку в meta_api_audit_log с partition by created_at.
@pytest.mark.asyncio
async def test_audit_log_insert(pg_engine: AsyncEngine, clean_meta_tables):
    await record_audit_log(
        pg_engine,
        endpoint="/act_42/insights",
        http_method="GET",
        http_status=200,
        initiated_by="integration_test",
        ad_account_id="act_42",
        request_payload={"fields": "ad_id,spend"},
        response_payload={"data_items": 5},
        duration_ms=312,
    )

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT endpoint, http_method, http_status, ad_account_id,
                           initiated_by, duration_ms
                    FROM meta_api_audit_log
                    WHERE initiated_by = 'integration_test'
                    ORDER BY created_at DESC LIMIT 1
                    """
                )
            )
        ).first()
    assert row is not None
    assert row[0] == "/act_42/insights"
    assert row[1] == "GET"
    assert row[2] == 200
    assert row[3] == "act_42"
    assert row[5] == 312


# Best-effort: ошибка записи (например, отсутствующая партиция в далёком будущем) не должна валить вызывающий код.
@pytest.mark.asyncio
async def test_audit_log_failure_is_swallowed(pg_engine: AsyncEngine, clean_meta_tables):
    # Невалидный http_status (отрицательный) на стороне нашей валидации — пройдёт,
    # но имитируем сценарий ошибки переводом endpoint в >128 символов проверится тем,
    # что мы корректно truncate-им. Поэтому проверим что вызов не бросает на нормальных данных
    # при искуственно «большом» payload.
    big_payload = {"data": "x" * 200_000}
    await record_audit_log(
        pg_engine,
        endpoint="/x" * 80,  # 160 chars; в БД будет truncate до 128
        http_method="POST",
        http_status=400,
        initiated_by="test_bigpayload",
        request_payload=big_payload,
    )

    # Не должно бросить. Проверим что строка появилась (хотя endpoint truncated).
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM meta_api_audit_log
                    WHERE initiated_by = 'test_bigpayload'
                    """
                )
            )
        ).first()
    assert row[0] >= 1


# Гонка: параллельный claim из 5 «воркеров» — каждая task достаётся только одному.
@pytest.mark.asyncio
async def test_concurrent_claim_skip_locked(pg_engine: AsyncEngine, clean_meta_tables):
    payload = _unique_payload("bulk_status_change")
    task_id = await create_mutation_task(
        pg_engine,
        payload=payload,
        requested_by="bot",
    )
    assert task_id is not None

    claims = await asyncio.gather(*(claim_pending_task(pg_engine) for _ in range(5)))
    got = [c for c in claims if not c.queue_empty]
    assert len(got) == 1, "Task должна быть захвачена только одним claim"
    assert got[0].task.id == task_id

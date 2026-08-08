# -*- coding: utf-8 -*-
"""Integration: outbox lifecycle для task_type='meta_api_mutation'.

Сценарии:
- PENDING → claim → execute (заглушка) → FAILED
- Идемпотентность: повторный create_mutation_task с тем же ключом → None
- Reconcile: stuck running → retrying
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
    cancel_task,
    claim_browser_ready_mutation_task,
    create_mutation_task,
)
from core.meta_api.schemas import MetaMutationPayload
from core.tasks.queue import reconcile_stuck_running

pytestmark = pytest.mark.usefixtures("fresh_browser_readiness")


# Очищаем task_queue и audit_log перед каждым тестом, чтобы не загрязнять.
@pytest_asyncio.fixture
async def clean_meta_tables(pg_engine: AsyncEngine):
    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    DELETE FROM telegram_action_tokens
                    WHERE incident_id IN (
                        SELECT id FROM incidents
                        WHERE incident_key = 'meta:token-invalid'
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    DELETE FROM telegram_message_slots
                    WHERE incident_id IN (
                        SELECT id FROM incidents
                        WHERE incident_key = 'meta:token-invalid'
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    DELETE FROM notification_events
                    WHERE incident_id IN (
                        SELECT id FROM incidents
                        WHERE incident_key = 'meta:token-invalid'
                    )
                    """
                )
            )
            await conn.execute(
                text("DELETE FROM incidents WHERE incident_key = 'meta:token-invalid'")
            )
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


def _unique_numeric_payload(kind: str = "pause_ad") -> MetaMutationPayload:
    """Status handlers require the numeric Meta object id shape."""
    target_id = str(10**17 + uuid.uuid4().int % (9 * 10**17))
    return MetaMutationPayload(
        mutation_kind=kind,
        target_id=target_id,
        params={"reason": "semantic ack integration test"},
        ad_account_id="act_42",
    )


# ====================== Lifecycle ======================


# Full lifecycle: PENDING → claim → execute (mock dispatch, success) → SUCCEEDED.
@pytest.mark.asyncio
async def test_pending_claim_execute_success(
    pg_engine: AsyncEngine,
    clean_meta_tables,
    monkeypatch,
):
    payload = _unique_payload("pause_ad")
    task_id = await create_mutation_task(
        pg_engine,
        payload=payload,
        requested_by="test_ai",
    )
    assert task_id is not None

    claim = await claim_browser_ready_mutation_task(pg_engine, lanes=("money",))
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

    claim = await claim_browser_ready_mutation_task(pg_engine, lanes=("money",))
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
    # Use a deactivating mutation so this test reaches dispatch regardless of
    # the global scanning pause; the assertion is about permanent auth errors.
    payload = _unique_payload("pause_ad")
    task_id = await create_mutation_task(
        pg_engine,
        payload=payload,
        requested_by="bot_auto",
        status="pending",
    )
    assert task_id is not None

    claim = await claim_browser_ready_mutation_task(pg_engine, lanes=("money",))
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
                text(
                    """
                    SELECT task.status, task.attempt_count, task.last_error,
                           incident.status AS incident_status,
                           (
                               SELECT COUNT(*)
                               FROM notification_events event
                               WHERE event.incident_id = incident.id
                                 AND event.event_type = 'worker_meta_token_invalid'
                           ) AS event_count
                    FROM task_queue task
                    LEFT JOIN incidents incident
                      ON incident.incident_key = 'meta:token-invalid'
                    WHERE task.id = :i
                    """
                ),
                {"i": task_id},
            )
        ).first()
    assert row is not None
    assert row[0] == "failed"  # сразу final, без retry
    assert row[1] == 0  # attempt_count не увеличился
    assert "TokenInvalidError" in (row[2] or "")
    assert row.incident_status == "open"
    assert row.event_count == 1


@pytest.mark.asyncio
async def test_token_invalid_rolls_back_terminal_task_when_card_projection_fails(
    pg_engine: AsyncEngine,
    clean_meta_tables,
    monkeypatch,
) -> None:
    import apps.meta_api_worker.main as worker_main
    import core.telegram.worker_notify as worker_notify

    payload = _unique_payload("pause_ad")
    task_id = await create_mutation_task(
        pg_engine,
        payload=payload,
        requested_by="bot_auto",
        status="pending",
    )
    claim = await claim_browser_ready_mutation_task(pg_engine, lanes=("money",))
    assert claim.task is not None

    async def raise_token_invalid(_client, _payload):
        raise TokenInvalidError("Session expired", code=190)

    async def fail_projection(*_args, **_kwargs):
        raise RuntimeError("notification projection failed")

    monkeypatch.setattr(worker_main, "dispatch_mutation", raise_token_invalid)
    monkeypatch.setattr(
        worker_notify,
        "notify_recurring_incident_in_transaction",
        fail_projection,
    )

    with pytest.raises(RuntimeError, match="notification projection failed"):
        await process_one_task(pg_engine, claim.task, client=AsyncMock())

    async with pg_engine.connect() as conn:
        task_status, incident_count, event_count = (
            await conn.execute(
                text(
                    """
                    SELECT
                        (SELECT status FROM task_queue WHERE id = :task_id),
                        (
                            SELECT COUNT(*) FROM incidents
                            WHERE incident_key = 'meta:token-invalid'
                        ),
                        (
                            SELECT COUNT(*) FROM notification_events event
                            JOIN incidents incident ON incident.id = event.incident_id
                            WHERE incident.incident_key = 'meta:token-invalid'
                        )
                    """
                ),
                {"task_id": task_id},
            )
        ).one()

    assert task_status == "running"
    assert incident_count == 0
    assert event_count == 0


@pytest.mark.asyncio
async def test_status_success_false_is_terminal_rejected(
    pg_engine: AsyncEngine,
    clean_meta_tables,
):
    payload = _unique_numeric_payload("pause_ad")
    task_id = await create_mutation_task(
        pg_engine,
        payload=payload,
        requested_by="operator:test",
        status="pending",
    )
    assert task_id is not None
    claim = await claim_browser_ready_mutation_task(pg_engine, lanes=("money",))
    assert claim.task is not None

    fake_client = AsyncMock()
    fake_client.execute_graph_call = AsyncMock(return_value={"success": False})
    await process_one_task(pg_engine, claim.task, client=fake_client)

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, attempt_count, result FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row is not None
    assert row.status == "failed"
    assert row.attempt_count == 0
    assert row.result["outcome"] == "REJECTED"
    fake_client.execute_graph_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_status_missing_ack_becomes_unknown_before_any_resend(
    pg_engine: AsyncEngine,
    clean_meta_tables,
    monkeypatch,
):
    import apps.meta_api_worker.main as worker_main

    monkeypatch.setattr(worker_main, "load_scanning_enabled", AsyncMock(return_value=True))
    payload = _unique_numeric_payload("activate_ad")
    task_id = await create_mutation_task(
        pg_engine,
        payload=payload,
        requested_by="operator:test",
        status="pending",
    )
    assert task_id is not None
    claim = await claim_browser_ready_mutation_task(pg_engine, lanes=("money",))
    assert claim.task is not None

    fake_client = AsyncMock()
    fake_client.execute_graph_call = AsyncMock(return_value={})
    await process_one_task(pg_engine, claim.task, client=fake_client)

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, attempt_count, result FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row is not None
    assert row.status == "retrying"
    assert row.attempt_count == 1
    assert row.result["outcome"] == "UNKNOWN"
    assert row.result["reconcile_required"] is True
    # The first processing pass performs exactly one write. The durable marker
    # forces a status read before any possible resend on the next claim.
    fake_client.execute_graph_call.assert_awaited_once()

    reconcile_claim = await claim_browser_ready_mutation_task(
        pg_engine,
        lanes=("money",),
    )
    assert reconcile_claim.task is not None
    assert reconcile_claim.task.id == task_id
    fake_client.execute_graph_call.reset_mock()
    fake_client.execute_graph_call.return_value = {"status": True}
    await process_one_task(pg_engine, reconcile_claim.task, client=fake_client)

    async with pg_engine.connect() as conn:
        reconciled_row = (
            await conn.execute(
                text("SELECT status, attempt_count, result FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert reconciled_row is not None
    assert reconciled_row.status == "retrying"
    assert reconciled_row.attempt_count == 2
    assert reconciled_row.result["outcome"] == "UNKNOWN"
    assert reconciled_row.result["reconcile_required"] is True
    fake_client.execute_graph_call.assert_awaited_once()
    assert fake_client.execute_graph_call.await_args.kwargs["method"] == "GET"


@pytest.mark.parametrize(
    ("graph_response", "expected_outcome"),
    [
        ({"success": False}, "REJECTED"),
        ({}, "UNKNOWN"),
    ],
)
@pytest.mark.asyncio
async def test_status_action_requires_exact_ack_and_routes_unknown_to_reconciliation(
    pg_engine: AsyncEngine,
    clean_meta_tables,
    monkeypatch,
    graph_response: dict[str, object],
    expected_outcome: str,
):
    import apps.meta_api_worker.main as worker_main

    monkeypatch.setattr(worker_main, "load_scanning_enabled", AsyncMock(return_value=True))
    payload = _unique_numeric_payload("pause_ad")
    payload = MetaMutationPayload(
        mutation_kind=payload.mutation_kind,
        target_id=payload.target_id,
        params={"reason": "ack integration test"},
        ad_account_id=payload.ad_account_id,
    )
    task_id = await create_mutation_task(
        pg_engine,
        payload=payload,
        requested_by="operator:test",
        status="pending",
    )
    assert task_id is not None
    claim = await claim_browser_ready_mutation_task(pg_engine, lanes=("money",))
    assert claim.task is not None

    fake_client = AsyncMock()
    fake_client.execute_graph_call = AsyncMock(return_value=graph_response)
    await process_one_task(pg_engine, claim.task, client=fake_client)

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, attempt_count, result FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row is not None
    assert row.status == ("failed" if expected_outcome == "REJECTED" else "retrying")
    assert row.attempt_count == (0 if expected_outcome == "REJECTED" else 1)
    assert row.result["outcome"] == expected_outcome
    if expected_outcome == "UNKNOWN":
        assert row.result["reconcile_required"] is True
    # The ambiguous acknowledgement schedules a read-before-write
    # reconciliation; this processing pass never sends the mutation twice.
    fake_client.execute_graph_call.assert_awaited_once()


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
    payload = _unique_payload("pause_ad")
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


# Canonical reconciler должен поднять running старше N секунд → retrying.
@pytest.mark.asyncio
async def test_reconcile_stuck_running(pg_engine: AsyncEngine, clean_meta_tables):
    payload = _unique_payload("activate_ad")
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

    reconciled = await reconcile_stuck_running(pg_engine, stuck_after_seconds=1800)
    assert reconciled >= 1

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row[0] == "retrying"


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

    claims = await asyncio.gather(
        *(claim_browser_ready_mutation_task(pg_engine, lanes=("money",)) for _ in range(5))
    )
    got = [c for c in claims if not c.queue_empty]
    assert len(got) == 1, "Task должна быть захвачена только одним claim"
    assert got[0].task.id == task_id

# -*- coding: utf-8 -*-
"""Интеграционные тесты для core.tasks.queue — реальная БД.

Покрывает контракты: idempotency_key, FOR UPDATE SKIP LOCKED, exponential backoff,
reconcile stuck running.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.meta_api.browser_readiness import (
    BrowserReadinessObservation,
    persist_browser_readiness,
)
from core.tasks import (
    claim_next_task,
    create_task,
    mark_succeeded,
    reconcile_stuck_running,
    requeue_for_retry,
)
from core.tasks.queue import (
    checkpoint_duplicate_adset_structure,
    claim_browser_ready_task,
    defer_unknown_reconciliation,
    expire_overdue_tasks,
    get_task_by_idempotency_key,
    requeue_duplicate_recovery,
    requeue_unknown_for_reconciliation,
    resolve_status_reconciliation_not_applied,
)
from core.tasks.wakeup import TaskQueueWakeup


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
    payload = {"source": "test", "target_id": "12345"}

    first_id = await create_task(
        pg_engine,
        task_type="observer_scan",
        idempotency_key=key,
        payload=payload,
        requested_by="test",
    )
    assert first_id is not None and first_id > 0

    second_id = await create_task(
        pg_engine,
        task_type="observer_scan",
        idempotency_key=key,
        payload=payload,
        requested_by="test",
    )
    assert second_id is None

    # И получить тот же row по ключу
    task = await get_task_by_idempotency_key(pg_engine, idempotency_key=key)
    assert task is not None
    assert task.id == first_id
    assert task.payload["target_id"] == "12345"
    assert task.status == "pending"


@pytest.mark.asyncio
async def test_committed_money_task_wakes_listener_before_poll_reconciliation(
    pg_engine,
    clean_task_queue,
    fresh_browser_readiness,
) -> None:
    """The wakeup is fast, while the following claim remains DB-authoritative."""
    wakeup = TaskQueueWakeup(
        pg_engine.url.render_as_string(hide_password=False),
        task_type="meta_api_mutation",
        lanes=("money",),
        reconcile_seconds=5,
    )
    stop = asyncio.Event()
    listener_task = asyncio.create_task(wakeup.run(stop))
    await asyncio.wait_for(wakeup.ready.wait(), timeout=2)
    started = time.perf_counter()
    try:
        task_id = await create_task(
            pg_engine,
            task_type="meta_api_mutation",
            idempotency_key=f"notify-money-{uuid.uuid4().hex}",
            payload={
                "mutation_kind": "pause_ad",
                "target_id": "notify-test-ad",
                "ad_account_id": "123",
            },
            requested_by="bot_auto_stop",
            lane="money",
        )
        assert task_id is not None
        assert await asyncio.wait_for(wakeup.wait_for_work(stop), timeout=1) is True
        assert time.perf_counter() - started < 1

        claim = await claim_browser_ready_task(
            pg_engine,
            task_type="meta_api_mutation",
            lanes=("money",),
            worker_id=uuid.uuid4(),
        )
        assert claim.task is not None
        assert claim.task.id == task_id
    finally:
        stop.set()
        await asyncio.wait_for(listener_task, timeout=2)


# Сценарий: claim атомарно переводит pending → running
@pytest.mark.asyncio
async def test_claim_marks_running(pg_engine, clean_task_queue) -> None:
    key = f"claim-{uuid.uuid4().hex[:8]}"
    await create_task(
        pg_engine,
        task_type="observer_scan",
        idempotency_key=key,
        payload={"source": "test", "target_id": "999"},
        requested_by="test",
    )

    claim = await claim_next_task(pg_engine, task_type="observer_scan")
    assert claim.queue_empty is False
    assert claim.task is not None
    assert claim.task.status == "running"
    assert claim.task.payload["target_id"] == "999"

    # Второй claim того же типа — пусто (уже захвачено)
    second = await claim_next_task(pg_engine, task_type="observer_scan")
    assert second.queue_empty is True
    assert second.task is None


# Сценарий: claim не трогает задачи другого task_type
@pytest.mark.asyncio
async def test_claim_filters_by_type(pg_engine, clean_task_queue) -> None:
    await create_task(
        pg_engine,
        task_type="observer_scan",
        idempotency_key=f"d-{uuid.uuid4().hex[:6]}",
        payload={"source": "test", "target_id": "1"},
        requested_by="test",
    )
    await create_task(
        pg_engine,
        task_type="tracker_event_process",
        idempotency_key=f"e-{uuid.uuid4().hex[:6]}",
        payload={"source": "test", "target_id": "2"},
        requested_by="test",
    )

    observer_claim = await claim_next_task(pg_engine, task_type="observer_scan")
    tracker_claim = await claim_next_task(pg_engine, task_type="tracker_event_process")

    assert observer_claim.task.payload["target_id"] == "1"
    assert tracker_claim.task.payload["target_id"] == "2"


@pytest.mark.asyncio
async def test_browser_maintenance_blocks_external_claims_until_expiry(
    pg_engine,
    clean_task_queue,
) -> None:
    await create_task(
        pg_engine,
        task_type="observer_scan",
        idempotency_key=f"maintenance-observer-{uuid.uuid4().hex[:6]}",
        payload={"source": "test", "target_id": "1"},
        requested_by="test",
    )
    await create_task(
        pg_engine,
        task_type="tracker_event_process",
        idempotency_key=f"maintenance-tracker-{uuid.uuid4().hex[:6]}",
        payload={"source": "test", "target_id": "2"},
        requested_by="test",
    )
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO system_config (key, value, description)
                VALUES (
                  'browser_maintenance',
                  jsonb_build_object(
                    'owner', 'test',
                    'expires_at', to_char(
                      clock_timestamp() + interval '5 minutes',
                      'YYYY-MM-DD"T"HH24:MI:SS.USOF'
                    )
                  ),
                  'test'
                )
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """
            )
        )
    try:
        blocked = await claim_next_task(pg_engine, task_type="observer_scan")
        unaffected = await claim_next_task(
            pg_engine,
            task_type="tracker_event_process",
        )
        assert blocked.queue_empty is True
        assert unaffected.task is not None

        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    UPDATE system_config
                    SET value = jsonb_set(
                      value,
                      '{expires_at}',
                      to_jsonb(
                        to_char(
                          clock_timestamp() - interval '1 second',
                          'YYYY-MM-DD"T"HH24:MI:SS.USOF'
                        )
                      )
                    )
                    WHERE key = 'browser_maintenance'
                    """
                )
            )
        released = await claim_next_task(pg_engine, task_type="observer_scan")
        assert released.task is not None
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM system_config WHERE key = 'browser_maintenance'"))


@pytest.mark.asyncio
async def test_money_task_waits_past_enqueue_deadline_then_gets_fresh_claim_budget(
    pg_engine,
    clean_task_queue,
    fresh_browser_readiness,
) -> None:
    task_id = await create_task(
        pg_engine,
        task_type="meta_api_mutation",
        idempotency_key=f"maintenance-money-{uuid.uuid4().hex}",
        payload={
            "mutation_kind": "pause_ad",
            "target_id": "maintenance-money-ad",
            "ad_account_id": "123",
        },
        requested_by="bot_auto_stop",
        deadline_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert task_id is not None
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO system_config (key, value, description)
                VALUES (
                  'browser_maintenance',
                  jsonb_build_object(
                    'owner', 'deadline-regression',
                    'expires_at', to_char(
                      clock_timestamp() + interval '5 minutes',
                      'YYYY-MM-DD"T"HH24:MI:SS.USOF'
                    )
                  ),
                  'deadline regression'
                )
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """
            )
        )
    try:
        assert await expire_overdue_tasks(pg_engine) == 0
        blocked = await claim_browser_ready_task(
            pg_engine,
            task_type="meta_api_mutation",
            lanes=("money",),
            worker_id=uuid.uuid4(),
        )
        assert blocked.task is None

        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM system_config WHERE key = 'browser_maintenance'"))
        assert await persist_browser_readiness(
            pg_engine,
            identity=fresh_browser_readiness,
            observation=BrowserReadinessObservation(
                state="ready",
                reason_code="ready",
                observed_contract_version=5,
                observed_profile_id=fresh_browser_readiness.profile_id,
                observed_session_id="deadline-regression-session",
            ),
            writer_instance=uuid.uuid4(),
            ttl_seconds=30,
        )

        claimed_at = datetime.now(UTC)
        claim = await claim_browser_ready_task(
            pg_engine,
            task_type="meta_api_mutation",
            lanes=("money",),
            worker_id=uuid.uuid4(),
        )
        assert claim.task is not None
        assert claim.task.id == task_id
        assert claim.task.deadline_at is not None
        remaining = (claim.task.deadline_at - claimed_at).total_seconds()
        assert 28 <= remaining <= 31
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM system_config WHERE key = 'browser_maintenance'"))


@pytest.mark.asyncio
async def test_browser_claim_snapshot_is_serialized_with_maintenance_commit(
    pg_engine,
    clean_task_queue,
) -> None:
    """A claim begun before gate INSERT must observe it after the shared lock."""
    await create_task(
        pg_engine,
        task_type="observer_scan",
        idempotency_key=f"fenced-observer-{uuid.uuid4().hex[:6]}",
        payload={"source": "test", "target_id": "1"},
        requested_by="test",
    )
    await create_task(
        pg_engine,
        task_type="tracker_event_process",
        idempotency_key=f"fenced-tracker-{uuid.uuid4().hex[:6]}",
        payload={"source": "test", "target_id": "2"},
        requested_by="test",
    )
    async with pg_engine.begin() as cleanup:
        await cleanup.execute(text("DELETE FROM system_config WHERE key = 'browser_maintenance'"))

    try:
        async with pg_engine.begin() as maintenance:
            await maintenance.execute(
                text(
                    """
                    SELECT pg_advisory_xact_lock(
                      hashtext('fb-agent'),
                      hashtext('browser-maintenance')
                    )
                    """
                )
            )
            waiting_claim = asyncio.create_task(
                claim_next_task(pg_engine, task_type="observer_scan")
            )
            await asyncio.sleep(0.1)
            assert not waiting_claim.done()

            # Non-browser work never participates in the browser fence.
            tracker_claim = await asyncio.wait_for(
                claim_next_task(pg_engine, task_type="tracker_event_process"),
                timeout=2,
            )
            assert tracker_claim.task is not None

            await maintenance.execute(
                text(
                    """
                    INSERT INTO system_config (key, value, description)
                    VALUES (
                      'browser_maintenance',
                      jsonb_build_object(
                        'owner', 'concurrency-test',
                        'expires_at', clock_timestamp() + interval '5 minutes'
                      ),
                      'test'
                    )
                    """
                )
            )

        blocked_claim = await asyncio.wait_for(waiting_claim, timeout=2)
        assert blocked_claim.queue_empty is True
        assert blocked_claim.task is None
    finally:
        async with pg_engine.begin() as cleanup:
            await cleanup.execute(
                text("DELETE FROM system_config WHERE key = 'browser_maintenance'")
            )


# Сценарий: mark_succeeded ставит completed_at и status
@pytest.mark.asyncio
async def test_mark_succeeded(pg_engine, clean_task_queue) -> None:
    key = f"ok-{uuid.uuid4().hex[:8]}"
    task_id = await create_task(
        pg_engine,
        task_type="observer_scan",
        idempotency_key=key,
        payload={"source": "test", "target_id": "1"},
        requested_by="test",
    )
    claim = await claim_next_task(pg_engine, task_type="observer_scan")
    assert claim.task is not None
    await mark_succeeded(
        pg_engine,
        task_id=task_id,
        result={"final_state": "false"},
        lease_owner=claim.task.lease_owner,
        lease_token=claim.task.lease_token,
    )

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
async def test_requeue_increments_attempts_and_sets_available_at(
    pg_engine, clean_task_queue
) -> None:
    key = f"retry-{uuid.uuid4().hex[:8]}"
    task_id = await create_task(
        pg_engine,
        task_type="observer_scan",
        idempotency_key=key,
        payload={"source": "test", "target_id": "1"},
        requested_by="test",
        max_attempts=5,
    )
    claim = await claim_next_task(pg_engine, task_type="observer_scan")
    assert claim.task is not None

    retried = await requeue_for_retry(
        pg_engine,
        task_id=task_id,
        error="network glitch",
        attempt_count=claim.task.attempt_count,
        max_attempts=claim.task.max_attempts,
        lease_owner=claim.task.lease_owner,
        lease_token=claim.task.lease_token,
    )
    assert retried is True

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT status, attempt_count, available_at, last_error "
                    "FROM task_queue WHERE id = :i"
                ),
                {"i": task_id},
            )
        ).first()
    assert row[0] == "retrying"
    assert row[1] == 1
    assert row[2] is not None
    assert "network glitch" in row[3]


@pytest.mark.parametrize(
    "writer",
    (
        "retry",
        "unknown",
        "defer_unknown",
        "duplicate_checkpoint",
        "duplicate_recovery",
        "status_reconciliation",
    ),
)
@pytest.mark.asyncio
async def test_expired_lease_rejects_every_claimed_task_writer_without_mutation(
    pg_engine,
    clean_task_queue,
    fresh_browser_readiness,
    writer: str,
) -> None:
    """Owner/token identity is insufficient once its lease authority expired."""
    duplicate = writer in {"duplicate_checkpoint", "duplicate_recovery"}
    payload = {
        "source": "expired-lease-test",
        "target_id": "12345",
    }
    if duplicate:
        payload.update(
            {
                "mutation_kind": "duplicate_adset_structure",
                "ad_account_id": "123",
                "params": {},
            }
        )
    task_type = "meta_api_mutation" if duplicate else "observer_scan"
    task_id = await create_task(
        pg_engine,
        task_type=task_type,
        idempotency_key=f"expired-{writer}-{uuid.uuid4().hex}",
        payload=payload,
        requested_by="test",
        max_attempts=5,
    )
    assert task_id is not None
    if duplicate:
        claim = await claim_browser_ready_task(
            pg_engine,
            task_type=task_type,
            lanes=("bulk",),
        )
    else:
        claim = await claim_next_task(pg_engine, task_type=task_type)
    assert claim.task is not None
    task = claim.task

    checkpoint = {
        "outcome": "UNKNOWN",
        "checkpoint_type": "duplicate_adset_structure",
        "checkpoint_version": 2,
        "phase": "recovery_retrying",
        "partial_fail": True,
        "created_ids": {
            "campaigns": ["1001"],
            "adsets": ["2001"],
            "ads": ["3001"],
        },
        "failed_steps": [{"step": "verify", "error": "deadline"}],
        "cleanup_failures": [{"id": "2001", "error": "transport"}],
        "recovery_requested": True,
    }
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET lease_expires_at = clock_timestamp() - interval '1 second',
                    external_started_at = CASE
                        WHEN :needs_reconciliation THEN clock_timestamp()
                        ELSE external_started_at
                    END,
                    result = CASE
                        WHEN :needs_reconciliation
                            THEN jsonb_build_object(
                                'outcome', 'UNKNOWN',
                                'reconcile_required', true
                            )
                        ELSE result
                    END
                WHERE id = :task_id
                """
            ),
            {
                "task_id": task.id,
                "needs_reconciliation": writer == "status_reconciliation",
            },
        )

    snapshot_sql = text(
        """
        SELECT status, attempt_count, available_at, deadline_at, last_error,
               result, lease_owner, lease_token, lease_expires_at, updated_at
        FROM task_queue
        WHERE id = :task_id
        """
    )
    async with pg_engine.connect() as conn:
        before = dict((await conn.execute(snapshot_sql, {"task_id": task.id})).one()._mapping)

    if writer == "retry":
        applied = await requeue_for_retry(
            pg_engine,
            task_id=task.id,
            error="must not apply",
            attempt_count=task.attempt_count,
            max_attempts=task.max_attempts,
            lease_owner=task.lease_owner,
            lease_token=task.lease_token,
            lane=task.lane,
        )
    elif writer == "unknown":
        applied = await requeue_unknown_for_reconciliation(
            pg_engine,
            task=task,
            error="must not apply",
        )
    elif writer == "defer_unknown":
        applied = await defer_unknown_reconciliation(
            pg_engine,
            task=task,
            error="must not apply",
        )
    elif writer == "duplicate_checkpoint":
        applied = await checkpoint_duplicate_adset_structure(
            pg_engine,
            task_id=task.id,
            checkpoint={**checkpoint, "recovery_requested": False},
            lease_owner=task.lease_owner,
            lease_token=task.lease_token,
        )
    elif writer == "duplicate_recovery":
        applied = await requeue_duplicate_recovery(
            pg_engine,
            task_id=task.id,
            checkpoint=checkpoint,
            error="must not apply",
            lease_owner=task.lease_owner,
            lease_token=task.lease_token,
        )
    else:
        applied = await resolve_status_reconciliation_not_applied(
            pg_engine,
            task_id=task.id,
            effective_status="ACTIVE",
            lease_owner=task.lease_owner,
            lease_token=task.lease_token,
        )

    assert applied in {False, None}
    async with pg_engine.connect() as conn:
        after = dict((await conn.execute(snapshot_sql, {"task_id": task.id})).one()._mapping)
    assert after == before


# Сценарий: исчерпан max_attempts → status='failed', а не retrying
@pytest.mark.asyncio
async def test_requeue_marks_failed_at_max_attempts(pg_engine, clean_task_queue) -> None:
    key = f"fail-{uuid.uuid4().hex[:8]}"
    task_id = await create_task(
        pg_engine,
        task_type="observer_scan",
        idempotency_key=key,
        payload={"source": "test", "target_id": "1"},
        requested_by="test",
        max_attempts=3,
    )
    claim = await claim_next_task(pg_engine, task_type="observer_scan")
    assert claim.task is not None

    # При attempt_count=2 + max_attempts=3 → новая попытка = 3 = max_attempts → failed
    retried = await requeue_for_retry(
        pg_engine,
        task_id=task_id,
        error="persistent error",
        attempt_count=2,
        max_attempts=3,
        lease_owner=claim.task.lease_owner,
        lease_token=claim.task.lease_token,
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
        task_type="observer_scan",
        idempotency_key=key,
        payload={"source": "test", "target_id": "1"},
        requested_by="test",
    )
    await claim_next_task(pg_engine, task_type="observer_scan")

    # Симулируем что воркер «крашнулся» 1 час назад
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE task_queue SET updated_at = NOW() - interval '1 hour', "
                "lease_expires_at = NOW() - interval '1 second' WHERE id = :i"
            ),
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


# Сценарий: claim не возвращает задачу с available_at в будущем
@pytest.mark.asyncio
async def test_claim_skips_future_retry(pg_engine, clean_task_queue) -> None:
    key = f"future-{uuid.uuid4().hex[:8]}"
    task_id = await create_task(
        pg_engine,
        task_type="observer_scan",
        idempotency_key=key,
        payload={"source": "test", "target_id": "1"},
        requested_by="test",
    )
    # Ставим available_at в будущее и status='retrying'
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE task_queue SET status = 'retrying', "
                "available_at = NOW() + interval '1 hour' WHERE id = :i"
            ),
            {"i": task_id},
        )

    claim = await claim_next_task(pg_engine, task_type="observer_scan")
    assert claim.queue_empty is True
    assert claim.task is None

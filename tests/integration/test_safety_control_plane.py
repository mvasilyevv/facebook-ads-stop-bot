"""Production-shape safety contracts for the PostgreSQL task control plane."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.tasks.queue import (
    claim_next_task,
    create_task,
    expire_overdue_tasks,
    mark_external_call_started,
    mark_succeeded,
    reconcile_stuck_running,
    request_task_cancel,
    requeue_unknown_for_reconciliation,
    resolve_status_reconciliation_not_applied,
)

pytestmark = pytest.mark.usefixtures("fresh_browser_readiness")


@pytest_asyncio.fixture
async def clean_safety_tasks(pg_engine):
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM task_queue"))
    yield
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM task_queue"))


async def _expire_lease(pg_engine, task_id: int) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET lease_expires_at = NOW() - INTERVAL '1 second',
                    deadline_at = NOW() - INTERVAL '1 second'
                WHERE id = :task_id
                """
            ),
            {"task_id": task_id},
        )


def _pause_payload(target_id: str) -> dict[str, object]:
    return {
        "mutation_kind": "pause_ad",
        "target_id": target_id,
        "ad_account_id": "123",
        "params": {},
    }


@pytest.mark.asyncio
async def test_concurrent_claims_are_unique_and_priority_ordered(
    pg_engine,
    clean_safety_tasks,
) -> None:
    task_ids: list[int] = []
    for priority in range(10):
        task_id = await create_task(
            pg_engine,
            task_type="tracker_event_process",
            idempotency_key=f"parallel-claim-{uuid.uuid4()}",
            payload={"event_id": priority},
            requested_by="test",
            lane="background",
            priority=priority,
        )
        assert task_id is not None
        task_ids.append(task_id)

    worker_ids = [uuid.uuid4() for _ in range(10)]
    claims = await asyncio.gather(
        *(
            claim_next_task(
                pg_engine,
                task_type="tracker_event_process",
                lanes=("background",),
                worker_id=worker_id,
            )
            for worker_id in worker_ids
        )
    )

    claimed = [claim.task for claim in claims]
    assert all(task is not None for task in claimed)
    assert {task.id for task in claimed if task is not None} == set(task_ids)
    assert {task.lease_owner for task in claimed if task is not None} == set(worker_ids)
    assert all(task.lease_token == 1 for task in claimed if task is not None)


@pytest.mark.asyncio
async def test_general_worker_cannot_claim_money_lane(pg_engine, clean_safety_tasks) -> None:
    task_id = await create_task(
        pg_engine,
        task_type="meta_api_mutation",
        idempotency_key=f"money-isolation-{uuid.uuid4()}",
        payload=_pause_payload("ad-money"),
        requested_by="bot_auto_stop",
    )
    assert task_id is not None

    general = await claim_next_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("interactive", "bulk", "background"),
        worker_id=uuid.uuid4(),
    )
    assert general.queue_empty is True

    money = await claim_next_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=uuid.uuid4(),
    )
    assert money.task is not None
    assert money.task.id == task_id
    assert money.task.lane == "money"


@pytest.mark.asyncio
async def test_crash_before_external_gets_fresh_deadline_and_stale_fence_is_rejected(
    pg_engine,
    clean_safety_tasks,
) -> None:
    task_id = await create_task(
        pg_engine,
        task_type="meta_api_mutation",
        idempotency_key=f"crash-before-send-{uuid.uuid4()}",
        payload=_pause_payload("ad-before"),
        requested_by="bot_auto_stop",
        max_attempts=3,
    )
    assert task_id is not None
    old_owner = uuid.uuid4()
    first = await claim_next_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=old_owner,
        lease_seconds=5,
    )
    assert first.task is not None
    old_token = first.task.lease_token

    await _expire_lease(pg_engine, task_id)
    assert await reconcile_stuck_running(pg_engine, stuck_after_seconds=0) == 1

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT status, attempt_count, deadline_at, lease_owner
                    FROM task_queue WHERE id = :task_id
                    """
                ),
                {"task_id": task_id},
            )
        ).one()
    assert row.status == "retrying"
    assert row.attempt_count == 1
    assert row.deadline_at > datetime.now(UTC)
    assert row.lease_owner is None

    new_owner = uuid.uuid4()
    second = await claim_next_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=new_owner,
    )
    assert second.task is not None
    assert second.task.lease_token == old_token + 1
    assert not await mark_succeeded(
        pg_engine,
        task_id=task_id,
        lease_owner=old_owner,
        lease_token=old_token,
    )
    assert await mark_succeeded(
        pg_engine,
        task_id=task_id,
        result={"outcome": "CONFIRMED"},
        lease_owner=new_owner,
        lease_token=second.task.lease_token,
    )


@pytest.mark.asyncio
async def test_crash_after_status_send_requires_reconciliation_before_retry(
    pg_engine,
    clean_safety_tasks,
) -> None:
    task_id = await create_task(
        pg_engine,
        task_type="meta_api_mutation",
        idempotency_key=f"crash-after-send-{uuid.uuid4()}",
        payload=_pause_payload("ad-after"),
        requested_by="bot_auto_stop",
    )
    assert task_id is not None
    owner = uuid.uuid4()
    claim = await claim_next_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=owner,
        lease_seconds=5,
    )
    assert claim.task is not None
    assert await mark_external_call_started(
        pg_engine,
        task_id=task_id,
        target_lock_key="ad-after",
        lease_owner=owner,
        lease_token=claim.task.lease_token,
    )

    await _expire_lease(pg_engine, task_id)
    assert await reconcile_stuck_running(pg_engine, stuck_after_seconds=0) == 1

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, result, deadline_at FROM task_queue WHERE id = :task_id"),
                {"task_id": task_id},
            )
        ).one()
    assert row.status == "retrying"
    assert row.result["outcome"] == "UNKNOWN"
    assert row.result["reconcile_required"] is True
    assert row.deadline_at > datetime.now(UTC)


@pytest.mark.asyncio
async def test_crash_after_non_status_send_is_terminal_unknown(
    pg_engine,
    clean_safety_tasks,
) -> None:
    task_id = await create_task(
        pg_engine,
        task_type="meta_api_mutation",
        idempotency_key=f"crash-after-create-{uuid.uuid4()}",
        payload={
            "mutation_kind": "set_budget",
            "target_id": "campaign-unknown",
            "ad_account_id": "123",
            "params": {"daily_budget": "1000"},
        },
        requested_by="test",
        lane="interactive",
    )
    assert task_id is not None
    owner = uuid.uuid4()
    claim = await claim_next_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("interactive",),
        worker_id=owner,
        lease_seconds=5,
    )
    assert claim.task is not None
    assert await mark_external_call_started(
        pg_engine,
        task_id=task_id,
        target_lock_key="campaign-unknown",
        lease_owner=owner,
        lease_token=claim.task.lease_token,
    )

    await _expire_lease(pg_engine, task_id)
    assert await reconcile_stuck_running(pg_engine, stuck_after_seconds=0) == 1

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, result FROM task_queue WHERE id = :task_id"),
                {"task_id": task_id},
            )
        ).one()
    assert row.status == "failed"
    assert row.result["outcome"] == "UNKNOWN"
    assert row.result["reconcile_required"] is True


@pytest.mark.asyncio
async def test_deadline_before_claim_is_rejected_not_unknown(pg_engine, clean_safety_tasks) -> None:
    task_id = await create_task(
        pg_engine,
        task_type="meta_api_mutation",
        idempotency_key=f"expired-before-claim-{uuid.uuid4()}",
        payload=_pause_payload("ad-expired"),
        requested_by="bot_auto_stop",
        deadline_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert task_id is not None
    assert await expire_overdue_tasks(pg_engine) == 1

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, result FROM task_queue WHERE id = :task_id"),
                {"task_id": task_id},
            )
        ).one()
    assert row.status == "failed"
    assert row.result["outcome"] == "REJECTED"


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary_change", ["cancel", "deadline"])
async def test_external_boundary_atomically_rejects_new_cancel_or_expired_deadline(
    pg_engine,
    clean_safety_tasks,
    boundary_change: str,
) -> None:
    target_id = f"ad-boundary-{boundary_change}-{uuid.uuid4()}"
    task_id = await create_task(
        pg_engine,
        task_type="meta_api_mutation",
        idempotency_key=f"boundary-{boundary_change}-{uuid.uuid4()}",
        payload=_pause_payload(target_id),
        requested_by="bot_auto_stop",
        lane="money",
    )
    assert task_id is not None
    claimed = await claim_next_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=uuid.uuid4(),
    )
    assert claimed.task is not None

    if boundary_change == "cancel":
        assert await request_task_cancel(
            pg_engine,
            task_id=task_id,
            reason="operator cancelled during preflight",
        )
    else:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE task_queue SET deadline_at = clock_timestamp() - "
                    "INTERVAL '1 second' WHERE id = :id"
                ),
                {"id": task_id},
            )

    assert not await mark_external_call_started(
        pg_engine,
        task_id=task_id,
        target_lock_key=target_id,
        lease_owner=claimed.task.lease_owner,
        lease_token=claimed.task.lease_token,
    )
    async with pg_engine.connect() as conn:
        external_started_at = (
            await conn.execute(
                text("SELECT external_started_at FROM task_queue WHERE id = :id"),
                {"id": task_id},
            )
        ).scalar_one()
    assert external_started_at is None


@pytest.mark.asyncio
async def test_cancel_after_unknown_waits_for_verified_status_read(
    pg_engine,
    clean_safety_tasks,
) -> None:
    task_id = await create_task(
        pg_engine,
        task_type="meta_api_mutation",
        idempotency_key=f"cancel-unknown-{uuid.uuid4()}",
        payload=_pause_payload("ad-cancel-unknown"),
        requested_by="bot_auto_stop",
        max_attempts=5,
    )
    assert task_id is not None
    first = await claim_next_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=uuid.uuid4(),
    )
    assert first.task is not None
    assert await mark_external_call_started(
        pg_engine,
        task_id=task_id,
        target_lock_key="ad-cancel-unknown",
        lease_owner=first.task.lease_owner,
        lease_token=first.task.lease_token,
    )
    assert await requeue_unknown_for_reconciliation(
        pg_engine,
        task=first.task,
        error="response lost",
    )
    assert await request_task_cancel(
        pg_engine,
        task_id=task_id,
        reason="operator cancelled",
    )

    second = await claim_next_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=uuid.uuid4(),
    )
    assert second.task is not None
    assert second.task.cancel_requested_at is not None
    resolution = await resolve_status_reconciliation_not_applied(
        pg_engine,
        task_id=task_id,
        effective_status="ACTIVE",
        lease_owner=second.task.lease_owner,
        lease_token=second.task.lease_token,
    )
    assert resolution == "cancelled"

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, result FROM task_queue WHERE id = :task_id"),
                {"task_id": task_id},
            )
        ).one()
    assert row.status == "cancelled"
    assert row.result["outcome"] == "REJECTED"
    assert row.result["reason"] == "cancelled_after_verified_not_applied"


@pytest.mark.asyncio
async def test_expired_reconciliation_deadline_stays_unknown(
    pg_engine,
    clean_safety_tasks,
) -> None:
    task_id = await create_task(
        pg_engine,
        task_type="meta_api_mutation",
        idempotency_key=f"expired-unknown-{uuid.uuid4()}",
        payload=_pause_payload("ad-expired-unknown"),
        requested_by="bot_auto_stop",
    )
    assert task_id is not None
    claim = await claim_next_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=uuid.uuid4(),
    )
    assert claim.task is not None
    assert await mark_external_call_started(
        pg_engine,
        task_id=task_id,
        target_lock_key="ad-expired-unknown",
        lease_owner=claim.task.lease_owner,
        lease_token=claim.task.lease_token,
    )
    assert await requeue_unknown_for_reconciliation(
        pg_engine,
        task=claim.task,
        error="response lost",
    )
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE task_queue SET deadline_at = NOW() - INTERVAL '1 second' WHERE id = :id"),
            {"id": task_id},
        )

    assert await expire_overdue_tasks(pg_engine) == 1
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, result FROM task_queue WHERE id = :task_id"),
                {"task_id": task_id},
            )
        ).one()
    assert row.status == "failed"
    assert row.result["outcome"] == "UNKNOWN"
    assert row.result["reconcile_required"] is True


@pytest.mark.asyncio
async def test_expired_lease_respects_max_attempts(pg_engine, clean_safety_tasks) -> None:
    task_id = await create_task(
        pg_engine,
        task_type="meta_api_mutation",
        idempotency_key=f"max-attempts-{uuid.uuid4()}",
        payload=_pause_payload("ad-max"),
        requested_by="bot_auto_stop",
        max_attempts=1,
    )
    assert task_id is not None
    claim = await claim_next_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=uuid.uuid4(),
        lease_seconds=5,
    )
    assert claim.task is not None
    await _expire_lease(pg_engine, task_id)

    assert await reconcile_stuck_running(pg_engine, stuck_after_seconds=0) == 1
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, result FROM task_queue WHERE id = :task_id"),
                {"task_id": task_id},
            )
        ).one()
    assert row.status == "failed"
    assert row.result == {
        "outcome": "REJECTED",
        "reason": "attempts_exhausted_before_external_start",
    }


@pytest.mark.asyncio
async def test_ambiguous_last_attempt_gets_one_read_reconciliation_claim(
    pg_engine,
    clean_safety_tasks,
) -> None:
    task_id = await create_task(
        pg_engine,
        task_type="meta_api_mutation",
        idempotency_key=f"last-attempt-unknown-{uuid.uuid4()}",
        payload=_pause_payload("ad-last-attempt"),
        requested_by="bot_auto_stop",
        max_attempts=1,
    )
    assert task_id is not None
    first = await claim_next_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=uuid.uuid4(),
    )
    assert first.task is not None
    assert await mark_external_call_started(
        pg_engine,
        task_id=task_id,
        target_lock_key="ad-last-attempt",
        lease_owner=first.task.lease_owner,
        lease_token=first.task.lease_token,
    )

    # Even though the mutation budget is exhausted, an ambiguous write always
    # receives one non-mutating status-read claim.
    assert await requeue_unknown_for_reconciliation(
        pg_engine,
        task=first.task,
        error="write response lost on nominal final attempt",
    )
    verification = await claim_next_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=uuid.uuid4(),
    )
    assert verification.task is not None
    assert verification.task.id == task_id
    assert verification.task.result["reconcile_required"] is True

    # If that read itself is unavailable, it becomes explicit terminal UNKNOWN
    # rather than a failed row that still advertises an unreachable follow-up.
    assert not await requeue_unknown_for_reconciliation(
        pg_engine,
        task=verification.task,
        error="verification read unavailable",
    )
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, result FROM task_queue WHERE id = :task_id"),
                {"task_id": task_id},
            )
        ).one()
    assert row.status == "failed"
    assert row.result["outcome"] == "UNKNOWN"
    assert row.result["reconcile_required"] is False
    assert row.result["reconciliation_exhausted"] is True


@pytest.mark.asyncio
async def test_last_attempt_verified_not_applied_is_terminal_without_resend(
    pg_engine,
    clean_safety_tasks,
) -> None:
    task_id = await create_task(
        pg_engine,
        task_type="meta_api_mutation",
        idempotency_key=f"last-attempt-not-applied-{uuid.uuid4()}",
        payload=_pause_payload("ad-last-not-applied"),
        requested_by="bot_auto_stop",
        max_attempts=1,
    )
    assert task_id is not None
    first = await claim_next_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=uuid.uuid4(),
    )
    assert first.task is not None
    assert await mark_external_call_started(
        pg_engine,
        task_id=task_id,
        target_lock_key="ad-last-not-applied",
        lease_owner=first.task.lease_owner,
        lease_token=first.task.lease_token,
    )
    assert await requeue_unknown_for_reconciliation(
        pg_engine,
        task=first.task,
        error="final write response lost",
    )
    verification = await claim_next_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=uuid.uuid4(),
    )
    assert verification.task is not None

    resolution = await resolve_status_reconciliation_not_applied(
        pg_engine,
        task_id=task_id,
        effective_status="ACTIVE",
        lease_owner=verification.task.lease_owner,
        lease_token=verification.task.lease_token,
    )
    assert resolution == "failed"
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT status, result, lease_owner, completed_at "
                    "FROM task_queue WHERE id = :task_id"
                ),
                {"task_id": task_id},
            )
        ).one()
    assert row.status == "failed"
    assert row.result["outcome"] == "REJECTED"
    assert row.result["reason"] == "attempts_exhausted_after_verified_not_applied"
    assert row.result["reconciled_after_unknown"] is True
    assert row.result["reconcile_required"] is False
    assert row.lease_owner is None
    assert row.completed_at is not None
    assert (
        await claim_next_task(
            pg_engine,
            task_type="meta_api_mutation",
            lanes=("money",),
            worker_id=uuid.uuid4(),
        )
    ).task is None


@pytest.mark.asyncio
async def test_stuck_bulk_last_attempt_still_gets_read_reconciliation(
    pg_engine,
    clean_safety_tasks,
) -> None:
    task_id = await create_task(
        pg_engine,
        task_type="meta_api_mutation",
        idempotency_key=f"stuck-bulk-last-attempt-{uuid.uuid4()}",
        payload={
            "mutation_kind": "bulk_status_change",
            "target_id": "bulk:last-attempt",
            "ad_account_id": "123",
            "params": {"action": "activate", "ad_ids": ["ad-bulk-last"]},
        },
        requested_by="owner:test-bulk-activate",
        max_attempts=1,
        lane="bulk",
    )
    assert task_id is not None
    claim = await claim_next_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("bulk",),
        worker_id=uuid.uuid4(),
        lease_seconds=5,
    )
    assert claim.task is not None
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET external_started_at = NOW(),
                    result = jsonb_build_object('diagnostic_marker', 'preserved'),
                    lease_expires_at = NOW() - INTERVAL '1 second'
                WHERE id = :task_id
                """
            ),
            {"task_id": task_id},
        )

    assert await reconcile_stuck_running(pg_engine, stuck_after_seconds=0) == 1
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, attempt_count, result FROM task_queue WHERE id = :task_id"),
                {"task_id": task_id},
            )
        ).one()
    assert row.status == "retrying"
    assert row.attempt_count == 1
    assert row.result["reconcile_required"] is True
    assert row.result["diagnostic_marker"] == "preserved"

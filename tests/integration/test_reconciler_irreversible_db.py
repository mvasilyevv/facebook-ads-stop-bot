# -*- coding: utf-8 -*-
"""Integration: crash-safe duplicate recovery and campaign creation fencing.

Требует Postgres из docker-compose (pg_engine fixture; skip если БД недоступна).
"""

from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

import core.meta_api.duplicate_incidents as duplicate_incidents
from core.meta_api.duplicate_incidents import duplicate_incident_key
from core.meta_api.schemas import IRREVERSIBLE_MUTATION_KINDS
from core.tasks import create_task
from core.tasks.queue import (
    fail_stuck_campaign_create,
    fail_stuck_duplicate_without_checkpoint,
    mark_failed,
    prepare_stuck_duplicate_recovery,
    reconcile_stuck_running,
    requeue_duplicate_recovery,
)


@pytest_asyncio.fixture
async def clean_task_queue(pg_engine):
    """Чистка task_queue до и после теста."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    DELETE FROM notification_events
                    WHERE incident_id IN (
                        SELECT id FROM incidents
                        WHERE incident_key LIKE 'task:duplicate-adset:%'
                           OR incident_key LIKE 'campaign-create:%'
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    DELETE FROM incidents
                    WHERE incident_key LIKE 'task:duplicate-adset:%'
                       OR incident_key LIKE 'campaign-create:%'
                    """
                )
            )
            await conn.execute(text("DELETE FROM task_queue"))

    await _truncate()
    yield
    await _truncate()


async def _make_stuck_running(pg_engine, *, mutation_kind: str) -> int:
    """Создаёт meta_api_mutation задачу и эмулирует зависание в 'running' 2 часа."""
    task_id = await create_task(
        pg_engine,
        task_type="meta_api_mutation",
        idempotency_key=f"irrev-{uuid.uuid4().hex[:10]}",
        payload={
            "mutation_kind": mutation_kind,
            "target_id": "act_test",
            "ad_account_id": "123",
            "params": {},
        },
        requested_by="test",
    )
    assert task_id is not None
    lease_owner = uuid.uuid4()
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'running', updated_at = NOW() - INTERVAL '2 hours',
                    lease_owner = :lease_owner, lease_token = 1,
                    lease_expires_at = NOW() - INTERVAL '1 hour'
                WHERE id = :i
                """
            ),
            {"i": task_id, "lease_owner": lease_owner},
        )
    return task_id


async def _status(pg_engine, task_id: int) -> str:
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(text("SELECT status FROM task_queue WHERE id = :i"), {"i": task_id})
        ).first()
    return str(row[0]) if row else ""


async def _fence(pg_engine, task_id: int):
    async with pg_engine.connect() as conn:
        return (
            await conn.execute(
                text("SELECT lease_owner, lease_token FROM task_queue WHERE id = :task_id"),
                {"task_id": task_id},
            )
        ).one()


async def _renew_fence(pg_engine, task_id: int) -> None:
    """Make a direct worker-finalization test own a currently valid lease."""
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET lease_expires_at = NOW() + INTERVAL '5 minutes'
                WHERE id = :task_id
                """
            ),
            {"task_id": task_id},
        )


def _partial_checkpoint(*, cleanup_failed: bool = False) -> dict:
    cleanup_failures = [{"id": "2001", "error": "transport"}] if cleanup_failed else []
    return {
        "outcome": "REJECTED",
        "checkpoint_type": "duplicate_adset_structure",
        "checkpoint_version": 2,
        "phase": "recovery_retrying" if cleanup_failed else "failed_cleanup",
        "partial_fail": True,
        "created_ids": {
            "campaigns": ["1001"],
            "adsets": ["2001"],
            "ads": ["3001"],
        },
        "failed_steps": [{"step": "verify", "error": "deadline"}],
        "cleanup_failures": cleanup_failures,
        "recovery_requested": cleanup_failed,
    }


@pytest.mark.asyncio
async def test_stale_checkpointed_duplicate_and_crashed_recovery_are_rescheduled(
    pg_engine, clean_task_queue
) -> None:
    """SIGKILL-equivalent recovery claim remains recoverable, never generic-failed."""
    task_id = await _make_stuck_running(
        pg_engine,
        mutation_kind="duplicate_adset_structure",
    )
    checkpoint = {
        "checkpoint_type": "duplicate_adset_structure",
        "checkpoint_version": 2,
        "phase": "activating",
        "created_ids": {"campaigns": ["1001"], "adsets": ["2001"], "ads": ["3001"]},
        "recovery_requested": True,
    }
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET result = CAST(:checkpoint AS JSONB),
                    updated_at = NOW() - INTERVAL '2 hours'
                WHERE id = :id
                """
            ),
            {"id": task_id, "checkpoint": json.dumps(checkpoint)},
        )

    assert await fail_stuck_duplicate_without_checkpoint(pg_engine) == 0
    assert await _status(pg_engine, task_id) == "running"

    assert await prepare_stuck_duplicate_recovery(pg_engine) == 1
    assert await _status(pg_engine, task_id) == "retrying"
    async with pg_engine.connect() as conn:
        assert (
            await conn.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM notification_events AS event
                    JOIN incidents AS incident ON incident.id = event.incident_id
                    WHERE incident.incident_key = :incident_key
                    """
                ),
                {"incident_key": duplicate_incident_key(task_id)},
            )
            == 1
        )
    # Recovery worker claims and itself dies: stale running + requested=true must
    # be moved back to retrying for another PAUSE-only attempt.
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'running', updated_at = NOW() - INTERVAL '2 hours'
                WHERE id = :id
                """
            ),
            {"id": task_id},
        )
    assert await prepare_stuck_duplicate_recovery(pg_engine) == 1
    assert await _status(pg_engine, task_id) == "retrying"
    async with pg_engine.connect() as conn:
        assert (
            await conn.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM notification_events AS event
                    JOIN incidents AS incident ON incident.id = event.incident_id
                    WHERE incident.incident_key = :incident_key
                    """
                ),
                {"incident_key": duplicate_incident_key(task_id)},
            )
            == 1
        )


@pytest.mark.asyncio
async def test_stale_duplicate_without_checkpoint_fails_without_replay(
    pg_engine, clean_task_queue
) -> None:
    task_id = await _make_stuck_running(
        pg_engine,
        mutation_kind="duplicate_adset_structure",
    )

    assert await prepare_stuck_duplicate_recovery(pg_engine) == 0
    assert await fail_stuck_duplicate_without_checkpoint(pg_engine) == 1
    assert await fail_stuck_duplicate_without_checkpoint(pg_engine) == 0
    assert await _status(pg_engine, task_id) == "failed"
    async with pg_engine.connect() as conn:
        incident = (
            await conn.execute(
                text(
                    """
                    SELECT incident.status, incident.severity,
                           COUNT(event.id) AS event_count
                    FROM incidents AS incident
                    JOIN notification_events AS event
                      ON event.incident_id = incident.id
                    WHERE incident.incident_key = :incident_key
                    GROUP BY incident.id, incident.status, incident.severity
                    """
                ),
                {"incident_key": duplicate_incident_key(task_id)},
            )
        ).one()
    assert incident.status == "open"
    assert incident.severity == "critical"
    assert incident.event_count == 1


@pytest.mark.asyncio
async def test_initial_partial_cleanup_failure_enters_recovery_without_existing_flag(
    pg_engine, clean_task_queue
) -> None:
    task_id = await _make_stuck_running(
        pg_engine,
        mutation_kind="duplicate_adset_structure",
    )
    existing = {
        "checkpoint_type": "duplicate_adset_structure",
        "checkpoint_version": 2,
        "phase": "failed_cleanup",
        "created_ids": {"campaigns": ["1001"], "adsets": ["2001"], "ads": []},
        # Deliberately no recovery_requested: this is the first cleanup failure.
    }
    incoming = {
        **existing,
        "phase": "recovery_retrying",
        "recovery_requested": True,
        "cleanup_failures": [{"id": "2001", "error": "transport"}],
    }
    async with pg_engine.connect() as conn:
        fence = (
            await conn.execute(
                text("SELECT lease_owner, lease_token FROM task_queue WHERE id = :id"),
                {"id": task_id},
            )
        ).one()
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET result = CAST(:checkpoint AS JSONB), updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": task_id, "checkpoint": json.dumps(existing)},
        )
    await _renew_fence(pg_engine, task_id)

    assert await requeue_duplicate_recovery(
        pg_engine,
        task_id=task_id,
        checkpoint=incoming,
        error="initial PAUSE cleanup failed",
        delay_seconds=1,
        lease_owner=fence.lease_owner,
        lease_token=fence.lease_token,
    )
    assert await _status(pg_engine, task_id) == "retrying"
    async with pg_engine.connect() as conn:
        result = (
            await conn.execute(
                text("SELECT result FROM task_queue WHERE id = :id"),
                {"id": task_id},
            )
        ).scalar_one()
    assert result["recovery_requested"] is True
    assert result["phase"] == "recovery_retrying"
    async with pg_engine.connect() as conn:
        incident = (
            await conn.execute(
                text(
                    """
                    SELECT incident.id, incident.status, COUNT(event.id) AS event_count
                    FROM incidents AS incident
                    JOIN notification_events AS event
                      ON event.incident_id = incident.id
                    WHERE incident.incident_key = :incident_key
                    GROUP BY incident.id, incident.status
                    """
                ),
                {"incident_key": duplicate_incident_key(task_id)},
            )
        ).one()
    assert incident.status == "open"
    assert incident.event_count == 1


@pytest.mark.asyncio
async def test_initial_partial_cleanup_failure_persists_when_result_was_never_checkpointed(
    pg_engine,
    clean_task_queue,
) -> None:
    task_id = await _make_stuck_running(
        pg_engine,
        mutation_kind="duplicate_adset_structure",
    )
    incoming = _partial_checkpoint(cleanup_failed=True)
    fence = await _fence(pg_engine, task_id)
    await _renew_fence(pg_engine, task_id)

    assert await requeue_duplicate_recovery(
        pg_engine,
        task_id=task_id,
        checkpoint=incoming,
        error="initial checkpoint write failed; PAUSE cleanup incomplete",
        delay_seconds=1,
        lease_owner=fence.lease_owner,
        lease_token=fence.lease_token,
    )
    assert await _status(pg_engine, task_id) == "retrying"

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT task.result, incident.status, COUNT(event.id) AS event_count
                    FROM task_queue AS task
                    JOIN incidents AS incident
                      ON incident.incident_key = :incident_key
                    JOIN notification_events AS event
                      ON event.incident_id = incident.id
                    WHERE task.id = :task_id
                    GROUP BY task.result, incident.id, incident.status
                    """
                ),
                {
                    "task_id": task_id,
                    "incident_key": duplicate_incident_key(task_id),
                },
            )
        ).one()
    assert row.result["created_ids"] == incoming["created_ids"]
    assert row.result["recovery_requested"] is True
    assert row.status == "open"
    assert row.event_count == 1


@pytest.mark.asyncio
async def test_initial_partial_cleanup_failure_with_stale_fence_persists_nothing(
    pg_engine,
    clean_task_queue,
) -> None:
    task_id = await _make_stuck_running(
        pg_engine,
        mutation_kind="duplicate_adset_structure",
    )
    incoming = _partial_checkpoint(cleanup_failed=True)
    fence = await _fence(pg_engine, task_id)

    assert not await requeue_duplicate_recovery(
        pg_engine,
        task_id=task_id,
        checkpoint=incoming,
        error="stale owner",
        delay_seconds=1,
        lease_owner=fence.lease_owner,
        lease_token=int(fence.lease_token) + 1,
    )
    assert await _status(pg_engine, task_id) == "running"
    async with pg_engine.connect() as conn:
        assert (
            await conn.scalar(
                text("SELECT COUNT(*) FROM incidents WHERE incident_key = :key"),
                {"key": duplicate_incident_key(task_id)},
            )
            == 0
        )


@pytest.mark.asyncio
async def test_duplicate_recovery_projection_failure_rolls_back_requeue(
    pg_engine,
    clean_task_queue,
    monkeypatch,
) -> None:
    task_id = await _make_stuck_running(
        pg_engine,
        mutation_kind="duplicate_adset_structure",
    )
    current = _partial_checkpoint(cleanup_failed=False)
    incoming = _partial_checkpoint(cleanup_failed=True)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE task_queue SET result = CAST(:result AS JSONB) WHERE id = :task_id"),
            {"task_id": task_id, "result": json.dumps(current)},
        )
    fence = await _fence(pg_engine, task_id)

    async def fail_projection(*args, **kwargs):
        raise RuntimeError("simulated crash at duplicate incident boundary")

    monkeypatch.setattr(
        duplicate_incidents,
        "project_duplicate_incident_in_transaction",
        fail_projection,
    )
    await _renew_fence(pg_engine, task_id)
    with pytest.raises(RuntimeError, match="duplicate incident boundary"):
        await requeue_duplicate_recovery(
            pg_engine,
            task_id=task_id,
            checkpoint=incoming,
            error="initial PAUSE cleanup failed",
            delay_seconds=1,
            lease_owner=fence.lease_owner,
            lease_token=fence.lease_token,
        )

    assert await _status(pg_engine, task_id) == "running"
    async with pg_engine.connect() as conn:
        assert (
            await conn.scalar(
                text("SELECT COUNT(*) FROM incidents WHERE incident_key = :key"),
                {"key": duplicate_incident_key(task_id)},
            )
            == 0
        )


@pytest.mark.asyncio
async def test_checkpoint_missing_projection_failure_rolls_back_terminal_transition(
    pg_engine,
    clean_task_queue,
    monkeypatch,
) -> None:
    task_id = await _make_stuck_running(
        pg_engine,
        mutation_kind="duplicate_adset_structure",
    )

    async def fail_projection(*args, **kwargs):
        raise RuntimeError("simulated crash at checkpoint-missing incident boundary")

    monkeypatch.setattr(
        duplicate_incidents,
        "project_duplicate_incident_in_transaction",
        fail_projection,
    )
    with pytest.raises(RuntimeError, match="checkpoint-missing incident boundary"):
        await fail_stuck_duplicate_without_checkpoint(pg_engine)

    assert await _status(pg_engine, task_id) == "running"
    async with pg_engine.connect() as conn:
        assert (
            await conn.scalar(
                text("SELECT COUNT(*) FROM incidents WHERE incident_key = :key"),
                {"key": duplicate_incident_key(task_id)},
            )
            == 0
        )


@pytest.mark.asyncio
async def test_duplicate_partial_projection_failure_rolls_back_terminal_transition(
    pg_engine,
    clean_task_queue,
    monkeypatch,
) -> None:
    task_id = await _make_stuck_running(
        pg_engine,
        mutation_kind="duplicate_adset_structure",
    )
    await _renew_fence(pg_engine, task_id)
    fence = await _fence(pg_engine, task_id)

    async def fail_projection(*args, **kwargs):
        raise RuntimeError("simulated crash before duplicate event commit")

    monkeypatch.setattr(
        duplicate_incidents,
        "project_duplicate_incident_in_transaction",
        fail_projection,
    )
    with pytest.raises(RuntimeError, match="duplicate event commit"):
        await mark_failed(
            pg_engine,
            task_id=task_id,
            error="partial duplicate",
            result=_partial_checkpoint(),
            lease_owner=fence.lease_owner,
            lease_token=fence.lease_token,
        )

    assert await _status(pg_engine, task_id) == "running"
    async with pg_engine.connect() as conn:
        assert (
            await conn.scalar(
                text("SELECT COUNT(*) FROM incidents WHERE incident_key = :key"),
                {"key": duplicate_incident_key(task_id)},
            )
            == 0
        )


@pytest.mark.asyncio
async def test_duplicate_partial_stale_fence_emits_no_incident(
    pg_engine,
    clean_task_queue,
) -> None:
    task_id = await _make_stuck_running(
        pg_engine,
        mutation_kind="duplicate_adset_structure",
    )
    actual_fence = await _fence(pg_engine, task_id)

    assert not await mark_failed(
        pg_engine,
        task_id=task_id,
        error="partial duplicate",
        result=_partial_checkpoint(),
        lease_owner=actual_fence.lease_owner,
        lease_token=int(actual_fence.lease_token) + 1,
    )
    assert await _status(pg_engine, task_id) == "running"
    async with pg_engine.connect() as conn:
        assert (
            await conn.scalar(
                text("SELECT COUNT(*) FROM incidents WHERE incident_key = :key"),
                {"key": duplicate_incident_key(task_id)},
            )
            == 0
        )


@pytest.mark.asyncio
async def test_duplicate_partial_terminal_projection_is_idempotent(
    pg_engine,
    clean_task_queue,
) -> None:
    task_id = await _make_stuck_running(
        pg_engine,
        mutation_kind="duplicate_adset_structure",
    )
    await _renew_fence(pg_engine, task_id)
    fence = await _fence(pg_engine, task_id)
    kwargs = {
        "task_id": task_id,
        "error": "partial duplicate",
        "result": _partial_checkpoint(),
        "lease_owner": fence.lease_owner,
        "lease_token": fence.lease_token,
    }

    assert await mark_failed(pg_engine, **kwargs)
    assert not await mark_failed(pg_engine, **kwargs)

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT COUNT(DISTINCT incident.id) AS incident_count,
                           COUNT(event.id) AS event_count
                    FROM incidents AS incident
                    JOIN notification_events AS event
                      ON event.incident_id = incident.id
                    WHERE incident.incident_key = :incident_key
                    """
                ),
                {"incident_key": duplicate_incident_key(task_id)},
            )
        ).one()
    assert row.incident_count == 1
    assert row.event_count == 1


# Контраст: зависший pause_ad (обратимая) → reconcile → retrying
@pytest.mark.asyncio
async def test_stuck_pause_ad_requeued_not_failed(pg_engine, clean_task_queue) -> None:
    task_id = await _make_stuck_running(pg_engine, mutation_kind="pause_ad")

    moved = await reconcile_stuck_running(pg_engine, exclude_kinds=IRREVERSIBLE_MUTATION_KINDS)
    assert moved == 1
    assert await _status(pg_engine, task_id) == "retrying"


# ====================== campaign_create (CRIT-1 + HIGH-3) ======================


async def _make_stuck_campaign_create(pg_engine) -> int:
    """Создаёт campaign_create задачу и эмулирует зависание в 'running' 2 часа."""
    task_id = await create_task(
        pg_engine,
        task_type="campaign_create",
        idempotency_key=f"cc-{uuid.uuid4().hex[:10]}",
        payload={"run_id": str(uuid.uuid4())},
        requested_by="test",
    )
    assert task_id is not None
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
    return task_id


# Зависший campaign_create → fail_stuck_campaign_create помечает failed (НЕ retry)
@pytest.mark.asyncio
async def test_stuck_campaign_create_marked_failed(pg_engine, clean_task_queue) -> None:
    task_id = await _make_stuck_campaign_create(pg_engine)
    async with pg_engine.connect() as conn:
        run_id = await conn.scalar(
            text("SELECT payload->>'run_id' FROM task_queue WHERE id = :task_id"),
            {"task_id": task_id},
        )

    n = await fail_stuck_campaign_create(pg_engine)

    assert n == 1
    assert await _status(pg_engine, task_id) == "failed"
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT incident.status, COUNT(event.id) AS event_count
                    FROM incidents AS incident
                    JOIN notification_events AS event
                      ON event.incident_id = incident.id
                    WHERE incident.incident_key = :incident_key
                    GROUP BY incident.id, incident.status
                    """
                ),
                {"incident_key": f"campaign-create:{run_id}:unknown"},
            )
        ).one()
    assert row.status == "open"
    assert row.event_count == 1


# CRIT-1: reconcile_stuck_running НЕ уводит campaign_create в retrying (даже без exclude_kinds)
@pytest.mark.asyncio
async def test_reconcile_does_not_retry_campaign_create(pg_engine, clean_task_queue) -> None:
    task_id = await _make_stuck_campaign_create(pg_engine)

    # Без exclude_kinds (meta) — campaign_create исключён безусловным task_type guard'ом.
    moved = await reconcile_stuck_running(pg_engine, exclude_kinds=None)

    assert moved == 0
    # campaign_create НЕ должна уйти в retrying — иначе риск дубля кампании
    assert await _status(pg_engine, task_id) == "running"


# Свежий fresh-run campaign_create (НЕ зависший) reconcile/fail не трогают
@pytest.mark.asyncio
async def test_fresh_campaign_create_untouched(pg_engine, clean_task_queue) -> None:
    task_id = await create_task(
        pg_engine,
        task_type="campaign_create",
        idempotency_key=f"cc-fresh-{uuid.uuid4().hex[:8]}",
        payload={"run_id": str(uuid.uuid4())},
        requested_by="test",
    )
    # status='pending', не running → ни одна из reconcile-функций не трогает.
    failed = await fail_stuck_campaign_create(pg_engine)
    moved = await reconcile_stuck_running(pg_engine, exclude_kinds=None)

    assert failed == 0
    assert moved == 0
    assert await _status(pg_engine, task_id) == "pending"

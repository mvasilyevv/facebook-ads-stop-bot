"""Durable observer-scan queue, cancellation and fencing contracts."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.commands.service import CommandService
from core.meta_api.freshness import defer_auto_stop_for_fresh_snapshot
from core.observer.scan_tasks import (
    ObserverScanCancelled,
    ObserverScanFenceLost,
    claim_observer_scan,
    enqueue_observer_scan,
    enqueue_scheduled_observer_scan,
    run_with_observer_scan_control,
)
from core.tasks import create_task, mark_succeeded
from core.tasks.queue import claim_browser_ready_task

pytestmark = pytest.mark.usefixtures("fresh_browser_readiness")


@pytest_asyncio.fixture
async def clean_observer_scan_tasks(pg_engine):
    async def _clean() -> None:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM task_queue "
                    "WHERE idempotency_key LIKE 'test-observer-scan:%' "
                    "OR idempotency_key LIKE 'test-auto-stop-refresh:%' "
                    "OR idempotency_key LIKE 'observer-scan:auto-stop-refresh:%' "
                    "OR idempotency_key LIKE 'observer-scan:scheduler:%'"
                )
            )

    await _clean()
    yield
    await _clean()


@asynccontextmanager
async def _observer_scanning_state(pg_engine, *, enabled: bool):
    """Временно выставляет observer_config.is_scanning_enabled, затем откатывает.

    Синглтон-строка общая на всю pytest-сессию (см. tests/integration/conftest.py
    и test_meta_api_freshness_db.py), поэтому тест обязан вернуть то состояние,
    что было до него, а не оставить TRUE/FALSE висеть для последующих тестов.
    """
    async with pg_engine.connect() as conn:
        previous = await conn.scalar(
            text("SELECT is_scanning_enabled FROM observer_config WHERE singleton_key = 'default'")
        )
    existed = previous is not None
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO observer_config (id, singleton_key, is_scanning_enabled)
                VALUES (gen_random_uuid(), 'default', :enabled)
                ON CONFLICT (singleton_key) DO UPDATE
                SET is_scanning_enabled = EXCLUDED.is_scanning_enabled
                """
            ),
            {"enabled": enabled},
        )
    try:
        yield
    finally:
        async with pg_engine.begin() as conn:
            if existed:
                await conn.execute(
                    text(
                        "UPDATE observer_config SET is_scanning_enabled = :previous "
                        "WHERE singleton_key = 'default'"
                    ),
                    {"previous": bool(previous)},
                )
            else:
                await conn.execute(
                    text("DELETE FROM observer_config WHERE singleton_key = 'default'")
                )


@pytest_asyncio.fixture
async def scanning_enabled(pg_engine):
    """observer_config.is_scanning_enabled = TRUE на время теста.

    С #Task7 (687c0ba7) enqueue_scheduled_observer_scan возвращает None и ничего
    не публикует, пока глобальное сканирование выключено — тестам, проверяющим
    саму публикацию задачи, нужно явно включить сканирование.
    """
    async with _observer_scanning_state(pg_engine, enabled=True):
        yield


@pytest_asyncio.fixture
async def scanning_disabled(pg_engine):
    """observer_config.is_scanning_enabled = FALSE на время теста (глобальный стоп)."""
    async with _observer_scanning_state(pg_engine, enabled=False):
        yield


@pytest.mark.asyncio
async def test_parallel_enqueue_and_claim_produce_exactly_one_execution(
    pg_engine,
    clean_observer_scan_tasks,
) -> None:
    key = f"test-observer-scan:{uuid.uuid4()}"
    receipts = await asyncio.gather(
        *(
            enqueue_observer_scan(
                pg_engine,
                requested_by="test_observer_scan",
                reason="concurrent acceptance",
                idempotency_key=key,
            )
            for _ in range(10)
        )
    )

    assert len({receipt.task_id for receipt in receipts}) == 1
    assert sum(receipt.created for receipt in receipts) == 1

    claims = await asyncio.gather(
        *(claim_observer_scan(pg_engine, worker_id=uuid.uuid4()) for _ in range(10))
    )
    claimed = [task for task in claims if task is not None]
    assert len(claimed) == 1
    assert claimed[0].id == receipts[0].task_id
    assert claimed[0].lease_owner is not None
    assert claimed[0].lease_token == 1


@pytest.mark.asyncio
async def test_parallel_operator_retries_share_one_interactive_scan(
    pg_engine,
    clean_observer_scan_tasks,
) -> None:
    service = CommandService(pg_engine)
    receipts = await asyncio.gather(
        *(
            service.enqueue_scan_retry(
                requested_by="integration_operator",
                idempotency_key=f"test-observer-scan:command:{uuid.uuid4()}",
            )
            for _ in range(10)
        )
    )

    assert len({receipt.task_id for receipt in receipts}) == 1
    assert sum(receipt.created for receipt in receipts) == 1
    assert {receipt.state for receipt in receipts} == {"queued"}
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) AS total, BOOL_AND(lane = 'interactive') AS interactive "
                    "FROM task_queue "
                    "WHERE requested_by = 'integration_operator'"
                ),
            )
        ).one()
    assert row.total == 1
    assert row.interactive is True


@pytest.mark.asyncio
async def test_scheduler_and_operator_retry_publish_one_interactive_scan(
    pg_engine,
    clean_observer_scan_tasks,
    scanning_enabled,
) -> None:
    scheduled, manual = await asyncio.gather(
        enqueue_scheduled_observer_scan(
            pg_engine,
            now=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
        ),
        CommandService(pg_engine).enqueue_scan_retry(
            requested_by="integration_operator",
            idempotency_key=f"test-observer-scan:command:{uuid.uuid4()}",
        ),
    )

    assert scheduled.task_id == manual.task_id
    assert sum((scheduled.created, manual.created)) == 1
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) AS total, MIN(lane) AS lane, MIN(priority) AS priority "
                    "FROM task_queue WHERE id = :task_id"
                ),
                {"task_id": manual.task_id},
            )
        ).one()
    assert row.total == 1
    assert row.lane == "interactive"
    assert row.priority == 75


@pytest.mark.asyncio
async def test_promoted_scheduled_scan_remains_reusable_by_scheduler(
    pg_engine,
    clean_observer_scan_tasks,
    scanning_enabled,
) -> None:
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    scheduled = await enqueue_scheduled_observer_scan(pg_engine, now=now)
    manual = await CommandService(pg_engine).enqueue_scan_retry(
        requested_by="integration_operator",
        idempotency_key=f"test-observer-scan:command:{uuid.uuid4()}",
    )
    next_tick = await enqueue_scheduled_observer_scan(pg_engine, now=now)

    assert scheduled.task_id == manual.task_id == next_tick.task_id
    assert scheduled.created is True
    assert manual.created is False
    assert next_tick.created is False
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT lane, priority FROM task_queue WHERE id = :task_id"),
                {"task_id": manual.task_id},
            )
        ).one()
    assert row.lane == "interactive"
    assert row.priority == 75


@pytest.mark.asyncio
async def test_scheduled_scan_reuses_only_the_outstanding_background_task(
    pg_engine,
    clean_observer_scan_tasks,
    scanning_enabled,
) -> None:
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    first, duplicate = await asyncio.gather(
        enqueue_scheduled_observer_scan(pg_engine, now=now),
        enqueue_scheduled_observer_scan(pg_engine, now=now),
    )

    assert first.task_id == duplicate.task_id
    assert sorted((first.created, duplicate.created)) == [False, True]

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET deadline_at = clock_timestamp() - interval '1 second'
                WHERE id = :task_id
                """
            ),
            {"task_id": first.task_id},
        )

    revived = await enqueue_scheduled_observer_scan(pg_engine, now=now)
    assert revived.task_id == first.task_id
    assert revived.created is False
    async with pg_engine.connect() as conn:
        singleton = (
            await conn.execute(
                text(
                    """
                    SELECT count(*) AS task_count,
                           bool_and(deadline_at > clock_timestamp()) AS deadline_live
                    FROM task_queue
                    WHERE task_type = 'observer_scan'
                      AND requested_by = 'observer_scheduler'
                      AND status IN ('pending', 'retrying', 'running')
                    """
                )
            )
        ).one()
    assert singleton.task_count == 1
    assert singleton.deadline_live is True

    claimed = await claim_observer_scan(pg_engine, worker_id=uuid.uuid4())
    assert claimed is not None
    assert claimed.id == first.task_id
    assert claimed.lane == "background"
    assert claimed.priority == 10

    still_running = await enqueue_scheduled_observer_scan(pg_engine, now=now)
    assert still_running.task_id == first.task_id
    assert still_running.created is False

    assert claimed.lease_owner is not None
    assert await mark_succeeded(
        pg_engine,
        task_id=claimed.id,
        result={"outcome": "CONFIRMED"},
        lease_owner=claimed.lease_owner,
        lease_token=claimed.lease_token,
    )

    next_tick = await enqueue_scheduled_observer_scan(pg_engine, now=now)
    assert next_tick.created is True
    assert next_tick.task_id != first.task_id


@pytest.mark.asyncio
async def test_scheduled_scan_publishes_nothing_while_scanning_disabled(
    pg_engine,
    clean_observer_scan_tasks,
    scanning_disabled,
) -> None:
    """Регрессия #Task7 (687c0ba7): на глобальном стопе scheduler ничего не публикует.

    До фикса каждый тик планировщика (~45с) создавал задачу, которая гарантированно
    заканчивалась outcome='paused' и оседала в операторской ленте как отказ — на
    проде за 4 часа накопилось 216 таких записей.
    """
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    async with pg_engine.connect() as conn:
        before_count = await conn.scalar(
            text(
                "SELECT COUNT(*) FROM task_queue "
                "WHERE task_type = 'observer_scan' AND requested_by = 'observer_scheduler'"
            )
        )

    result = await enqueue_scheduled_observer_scan(pg_engine, now=now)

    assert result is None
    async with pg_engine.connect() as conn:
        after_count = await conn.scalar(
            text(
                "SELECT COUNT(*) FROM task_queue "
                "WHERE task_type = 'observer_scan' AND requested_by = 'observer_scheduler'"
            )
        )
    assert after_count == before_count


@pytest.mark.asyncio
async def test_dependency_barrier_is_not_claimed_until_every_child_is_terminal(
    pg_engine,
    clean_observer_scan_tasks,
) -> None:
    child_ids: list[int] = []
    for suffix in ("first", "second"):
        task_id = await create_task(
            pg_engine,
            task_type="meta_api_mutation",
            idempotency_key=f"test-observer-scan:{uuid.uuid4()}:{suffix}",
            payload={
                "mutation_kind": "bulk_status_change",
                "target_id": f"auto-pause:{suffix}",
                "ad_account_id": "123",
                "params": {"action": "pause", "ad_ids": ["238001"]},
            },
            requested_by="bot_auto_stop",
            lane="money",
        )
        assert task_id is not None
        child_ids.append(task_id)

    receipt = await enqueue_observer_scan(
        pg_engine,
        requested_by="test_observer_scan",
        reason="dependency barrier acceptance",
        idempotency_key=f"test-observer-scan:{uuid.uuid4()}",
        dependency_task_ids=child_ids,
    )

    assert await claim_observer_scan(pg_engine, worker_id=uuid.uuid4()) is None
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE task_queue SET status = 'succeeded', completed_at = NOW() "
                "WHERE id = :task_id"
            ),
            {"task_id": child_ids[0]},
        )
    assert await claim_observer_scan(pg_engine, worker_id=uuid.uuid4()) is None

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE task_queue SET status = 'failed', completed_at = NOW() WHERE id = :task_id"
            ),
            {"task_id": child_ids[1]},
        )
    claimed = await claim_observer_scan(pg_engine, worker_id=uuid.uuid4())

    assert claimed is not None
    assert claimed.id == receipt.task_id
    assert claimed.payload["dependency_state"] == "ready"
    assert claimed.payload["dependency_task_ids"] == child_ids


@pytest.mark.asyncio
async def test_dependency_barrier_survives_retention_removing_its_children(
    pg_engine,
    clean_observer_scan_tasks,
) -> None:
    """Уборка очереди не должна оставлять барьер запаркованным навсегда.

    Скан с зависимостями паркуется на сто лет вперёд и снимается ТОЛЬКО когда
    все дети терминальны. Ретенция штатно удаляет терминальные задачи, и
    удалённая строка раньше считалась незавершённой — то есть барьер, чьи дети
    дожили до уборки, не снялся бы уже никогда. Пропавшая строка означает
    завершённую работу: уборка не трогает pending/running/retrying.
    """
    child_id = await create_task(
        pg_engine,
        task_type="meta_api_mutation",
        idempotency_key=f"test-observer-scan:{uuid.uuid4()}:collected",
        payload={
            "mutation_kind": "bulk_status_change",
            "target_id": "auto-pause:collected",
            "ad_account_id": "123",
            "params": {"action": "pause", "ad_ids": ["238002"]},
        },
        requested_by="bot_auto_stop",
        lane="money",
    )
    assert child_id is not None

    receipt = await enqueue_observer_scan(
        pg_engine,
        requested_by="test_observer_scan",
        reason="dependency barrier after retention",
        idempotency_key=f"test-observer-scan:{uuid.uuid4()}",
        dependency_task_ids=[child_id],
    )

    assert await claim_observer_scan(pg_engine, worker_id=uuid.uuid4()) is None

    # Ребёнок завершился и через положенный срок был убран ретенцией.
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM task_queue WHERE id = :task_id"),
            {"task_id": child_id},
        )

    claimed = await claim_observer_scan(pg_engine, worker_id=uuid.uuid4())

    assert claimed is not None
    assert claimed.id == receipt.task_id
    assert claimed.payload["dependency_state"] == "ready"


@pytest.mark.asyncio
async def test_inflight_cancel_stops_operation_from_database_authority(
    pg_engine,
    clean_observer_scan_tasks,
) -> None:
    receipt = await enqueue_observer_scan(
        pg_engine,
        requested_by="test_observer_scan",
        reason="cancel acceptance",
        idempotency_key=f"test-observer-scan:{uuid.uuid4()}",
    )
    task = await claim_observer_scan(pg_engine, worker_id=uuid.uuid4())
    assert task is not None and task.id == receipt.task_id

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def operation() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    controlled = asyncio.create_task(
        run_with_observer_scan_control(
            pg_engine,
            task,
            operation,
            poll_interval_seconds=0.01,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE task_queue SET cancel_requested_at = NOW(), "
                "cancel_reason = 'integration test' WHERE id = :task_id"
            ),
            {"task_id": task.id},
        )

    with pytest.raises(ObserverScanCancelled, match="cancel_requested"):
        await asyncio.wait_for(controlled, timeout=2)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_stale_fence_cancels_operation_and_cannot_continue(
    pg_engine,
    clean_observer_scan_tasks,
) -> None:
    receipt = await enqueue_observer_scan(
        pg_engine,
        requested_by="test_observer_scan",
        reason="fence acceptance",
        idempotency_key=f"test-observer-scan:{uuid.uuid4()}",
    )
    task = await claim_observer_scan(pg_engine, worker_id=uuid.uuid4())
    assert task is not None and task.id == receipt.task_id
    started = asyncio.Event()

    async def operation() -> None:
        started.set()
        await asyncio.Event().wait()

    controlled = asyncio.create_task(
        run_with_observer_scan_control(
            pg_engine,
            task,
            operation,
            poll_interval_seconds=0.01,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE task_queue SET lease_token = lease_token + 1 WHERE id = :task_id"),
            {"task_id": task.id},
        )

    with pytest.raises(ObserverScanFenceLost, match="lost its lease"):
        await asyncio.wait_for(controlled, timeout=2)


@pytest.mark.asyncio
async def test_stale_autostop_deferral_atomically_enqueues_scan(
    pg_engine,
    clean_observer_scan_tasks,
) -> None:
    task_id = await create_task(
        pg_engine,
        task_type="meta_api_mutation",
        idempotency_key=f"test-auto-stop-refresh:{uuid.uuid4()}",
        payload={
            "mutation_kind": "pause_ad",
            "target_id": "238001",
            "ad_account_id": "123",
            "params": {},
        },
        requested_by="test_auto_stop_refresh",
        lane="money",
    )
    assert task_id is not None
    claim = await claim_browser_ready_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=uuid.uuid4(),
    )
    assert claim.task is not None and claim.task.id == task_id

    deferred = await defer_auto_stop_for_fresh_snapshot(
        pg_engine,
        task_id=task_id,
        lease_owner=claim.task.lease_owner,
        lease_token=claim.task.lease_token,
    )
    assert deferred is True

    async with pg_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT task_type, status, lane, requested_by, payload "
                    "FROM task_queue WHERE id = :task_id OR requested_by = 'meta_api_worker' "
                    "ORDER BY id"
                ),
                {"task_id": task_id},
            )
        ).all()
    assert len(rows) == 2
    assert rows[0].task_type == "meta_api_mutation"
    assert rows[0].status == "retrying"
    assert rows[1].task_type == "observer_scan"
    assert rows[1].status == "pending"
    assert rows[1].lane == "interactive"
    assert rows[1].payload == {"reason": "auto_stop_requires_fresh_meta"}


@pytest.mark.asyncio
async def test_expired_autostop_lease_cannot_defer_or_enqueue_scan(
    pg_engine,
    clean_observer_scan_tasks,
) -> None:
    task_id = await create_task(
        pg_engine,
        task_type="meta_api_mutation",
        idempotency_key=f"test-expired-auto-stop-refresh:{uuid.uuid4()}",
        payload={
            "mutation_kind": "pause_ad",
            "target_id": "238002",
            "ad_account_id": "123",
            "params": {},
        },
        requested_by="test_expired_auto_stop_refresh",
        lane="money",
    )
    assert task_id is not None
    claim = await claim_browser_ready_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=uuid.uuid4(),
    )
    assert claim.task is not None and claim.task.id == task_id
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET lease_expires_at = clock_timestamp() - interval '1 second'
                WHERE id = :task_id
                """
            ),
            {"task_id": task_id},
        )
    async with pg_engine.connect() as conn:
        before = (
            await conn.execute(
                text(
                    """
                    SELECT status, available_at, deadline_at, last_error,
                           lease_owner, lease_token, lease_expires_at, updated_at
                    FROM task_queue
                    WHERE id = :task_id
                    """
                ),
                {"task_id": task_id},
            )
        ).one()

    deferred = await defer_auto_stop_for_fresh_snapshot(
        pg_engine,
        task_id=task_id,
        lease_owner=claim.task.lease_owner,
        lease_token=claim.task.lease_token,
    )

    assert deferred is False
    async with pg_engine.connect() as conn:
        after = (
            await conn.execute(
                text(
                    """
                    SELECT status, available_at, deadline_at, last_error,
                           lease_owner, lease_token, lease_expires_at, updated_at
                    FROM task_queue
                    WHERE id = :task_id
                    """
                ),
                {"task_id": task_id},
            )
        ).one()
        refresh_count = await conn.scalar(
            text(
                """
                SELECT COUNT(*)
                FROM task_queue
                WHERE requested_by = 'meta_api_worker'
                  AND payload->>'reason' = 'auto_stop_requires_fresh_meta'
                """
            )
        )
    assert after == before
    assert refresh_count == 0

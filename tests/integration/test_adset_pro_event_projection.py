"""Postgres integration for event ordering, retry and cancellation boundary."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.adset_pro.ingest import ingest_postback
from core.adset_pro.processing import (
    TrackerLeaseLostError,
    claim_event_tasks,
    mark_task_retry,
    process_event_task,
)
from core.adset_pro.schemas import PostbackEvent
from core.tasks.queue import (
    claim_browser_ready_task,
    create_task,
    mark_external_call_started,
    reconcile_stuck_running,
    request_task_cancel,
    requeue_proven_not_committed,
)

pytestmark = pytest.mark.usefixtures("fresh_browser_readiness")


@pytest_asyncio.fixture
async def clean_event_projection(pg_engine):
    async def _clean() -> None:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM task_queue WHERE task_type = 'tracker_event_process'")
            )
            await conn.execute(
                text(
                    "DELETE FROM task_queue WHERE idempotency_key LIKE 'auto-open-%' "
                    "OR idempotency_key LIKE 'auto-started-%' "
                    "OR idempotency_key LIKE 'auto-shadow-%' "
                    "OR idempotency_key LIKE 'manual-open-%'"
                )
            )
            await conn.execute(text("DELETE FROM tracker_click_state"))
            await conn.execute(text("DELETE FROM adsetpro_postback_events"))

    await _clean()
    yield
    await _clean()


async def _fb_ad_id(pg_engine, ad_id) -> str:
    async with pg_engine.connect() as conn:
        return str(
            await conn.scalar(text("SELECT fb_ad_id FROM fb_ads WHERE id = :id"), {"id": ad_id})
        )


def _event(click_id: str, fb_ad_id: str, event_type: str) -> PostbackEvent:
    now = datetime.now(UTC)
    return PostbackEvent(
        click_id=click_id,
        fb_ad_id=fb_ad_id,
        event_type=event_type,
        revenue=Decimal("10") if event_type == "ftd" else Decimal("0"),
        currency="USD",
        received_at=now,
        occurred_at=now,
        raw={"sub8": fb_ad_id, "country": "KE"},
    )


async def _drain_one(pg_engine):
    claimed = await claim_event_tasks(pg_engine)
    assert len(claimed) == 1
    result = await process_event_task(
        pg_engine,
        claim=claimed[0],
    )
    assert result.processed is True
    return result


@pytest.mark.asyncio
async def test_tracker_claim_populates_background_deadline_and_fence(
    pg_engine,
    fb_ad_fixture,
    clean_event_projection,
) -> None:
    fb_ad_id = await _fb_ad_id(pg_engine, fb_ad_fixture.ad_id)
    ingested = await ingest_postback(
        pg_engine,
        _event(f"claim-metadata-{uuid.uuid4().hex}", fb_ad_id, "registration"),
    )
    worker_id = uuid.uuid4()

    claim = (await claim_event_tasks(pg_engine, worker_id=worker_id))[0]

    assert claim.task_id == ingested.task_id
    assert claim.lease_owner == worker_id
    assert claim.lease_token == 1
    assert claim.deadline_at > datetime.now(UTC)
    assert claim.lease_expires_at > datetime.now(UTC)
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT status, lane, priority, available_at, deadline_at,
                           lease_owner, lease_token, lease_expires_at, correlation_id
                    FROM task_queue
                    WHERE id = :task_id
                    """
                ),
                {"task_id": claim.task_id},
            )
        ).one()
    assert row.status == "running"
    assert row.lane == "background"
    assert row.priority == 0
    assert row.available_at is not None
    assert row.deadline_at == claim.deadline_at
    assert row.lease_owner == worker_id
    assert row.lease_token == claim.lease_token
    assert row.lease_expires_at == claim.lease_expires_at
    assert row.correlation_id is not None


@pytest.mark.asyncio
async def test_stale_tracker_worker_cannot_project_retry_or_finalize_after_reclaim(
    pg_engine,
    fb_ad_fixture,
    clean_event_projection,
) -> None:
    fb_ad_id = await _fb_ad_id(pg_engine, fb_ad_fixture.ad_id)
    event = _event(f"stale-fence-{uuid.uuid4().hex}", fb_ad_id, "registration")
    ingested = await ingest_postback(pg_engine, event)
    stale_claim = (await claim_event_tasks(pg_engine, worker_id=uuid.uuid4()))[0]
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET lease_expires_at = now() - interval '1 second'
                WHERE id = :task_id
                """
            ),
            {"task_id": stale_claim.task_id},
        )

    assert await reconcile_stuck_running(pg_engine, stuck_after_seconds=0) == 1
    current_claim = (await claim_event_tasks(pg_engine, worker_id=uuid.uuid4()))[0]
    assert current_claim.task_id == stale_claim.task_id == ingested.task_id
    assert current_claim.lease_token == stale_claim.lease_token + 1

    assert await mark_task_retry(pg_engine, claim=stale_claim, error="stale worker") is False
    with pytest.raises(TrackerLeaseLostError):
        await process_event_task(pg_engine, claim=stale_claim)

    async with pg_engine.connect() as conn:
        before = (
            await conn.execute(
                text(
                    """
                    SELECT q.status, q.lease_owner, q.lease_token, e.processed_at,
                           (SELECT COUNT(*) FROM tracker_click_state
                            WHERE source = 'adsetpro' AND click_id = :click_id) AS states
                    FROM task_queue q
                    JOIN adsetpro_postback_events e ON e.id = :event_id
                    WHERE q.id = :task_id
                    """
                ),
                {
                    "task_id": current_claim.task_id,
                    "event_id": ingested.event_id,
                    "click_id": event.click_id,
                },
            )
        ).one()
    assert before.status == "running"
    assert before.lease_owner == current_claim.lease_owner
    assert before.lease_token == current_claim.lease_token
    assert before.processed_at is None
    assert before.states == 0

    assert (await process_event_task(pg_engine, claim=current_claim)).processed is True


@pytest.mark.asyncio
async def test_running_tracker_cancel_is_honored_before_projection(
    pg_engine,
    fb_ad_fixture,
    clean_event_projection,
) -> None:
    fb_ad_id = await _fb_ad_id(pg_engine, fb_ad_fixture.ad_id)
    event = _event(f"cancel-fence-{uuid.uuid4().hex}", fb_ad_id, "registration")
    ingested = await ingest_postback(pg_engine, event)
    claim = (await claim_event_tasks(pg_engine, worker_id=uuid.uuid4()))[0]

    assert await request_task_cancel(
        pg_engine,
        task_id=claim.task_id,
        reason="operator_cancelled_tracker_projection",
    )
    result = await process_event_task(pg_engine, claim=claim)

    assert result.processed is False
    assert result.attribution_status == "cancelled"
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT q.status, q.cancel_requested_at, q.lease_owner,
                           e.processed_at,
                           (SELECT COUNT(*) FROM tracker_click_state
                            WHERE source = 'adsetpro' AND click_id = :click_id) AS states
                    FROM task_queue q
                    JOIN adsetpro_postback_events e ON e.id = :event_id
                    WHERE q.id = :task_id
                    """
                ),
                {
                    "task_id": ingested.task_id,
                    "event_id": ingested.event_id,
                    "click_id": event.click_id,
                },
            )
        ).one()
    assert row.status == "cancelled"
    assert row.cancel_requested_at is not None
    assert row.lease_owner is None
    assert row.processed_at is None
    assert row.states == 0
    assert await claim_event_tasks(pg_engine) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("order", [("registration", "ftd"), ("ftd", "registration")])
async def test_registration_ftd_pair_confirms_in_any_order(
    pg_engine,
    fb_ad_fixture,
    clean_event_projection,
    order: tuple[str, str],
) -> None:
    fb_ad_id = await _fb_ad_id(pg_engine, fb_ad_fixture.ad_id)
    click_id = f"pair-{uuid.uuid4().hex}"
    for event_type in order:
        await ingest_postback(pg_engine, _event(click_id, fb_ad_id, event_type))
        result = await _drain_one(pg_engine)
        assert result.processed is True

    async with pg_engine.connect() as conn:
        state = (
            await conn.execute(
                text(
                    """
                    SELECT registration, ftd, confirmed_deposit, registration_at, ftd_at
                    FROM tracker_click_state
                    WHERE source = 'adsetpro' AND click_id = :click_id
                    """
                ),
                {"click_id": click_id},
            )
        ).one()
    assert state.registration is True
    assert state.ftd is True
    assert state.confirmed_deposit is True
    assert state.registration_at is not None and state.ftd_at is not None


@pytest.mark.asyncio
async def test_same_click_conflicting_direct_ads_is_quarantined_without_cross_ad_merge(
    pg_engine,
    fb_ad_fixture,
    clean_event_projection,
) -> None:
    first_fb_ad_id = await _fb_ad_id(pg_engine, fb_ad_fixture.ad_id)
    second_ad_id = uuid.uuid4()
    second_fb_ad_id = f"239{uuid.uuid4().int % 10**12:012d}"
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name)
                VALUES (:id, :adset_id, :fb_ad_id, 'conflicting ad')
                """
            ),
            {
                "id": second_ad_id,
                "adset_id": fb_ad_fixture.adset_id,
                "fb_ad_id": second_fb_ad_id,
            },
        )

    click_id = f"conflict-{uuid.uuid4().hex}"
    await ingest_postback(pg_engine, _event(click_id, first_fb_ad_id, "registration"))
    await _drain_one(pg_engine)
    await ingest_postback(pg_engine, _event(click_id, second_fb_ad_id, "ftd"))
    claim = (await claim_event_tasks(pg_engine))[0]

    conflicted = await process_event_task(pg_engine, claim=claim)

    assert conflicted.processed is False
    assert conflicted.attribution_status == "ambiguous"
    async with pg_engine.connect() as conn:
        task_status = await conn.scalar(
            text("SELECT status FROM task_queue WHERE id = :id"), {"id": claim.task_id}
        )
        event_status = await conn.scalar(
            text(
                """
                SELECT attribution_status
                FROM adsetpro_postback_events
                WHERE source = 'adsetpro' AND click_id = :click_id AND event_type = 'ftd'
                """
            ),
            {"click_id": click_id},
        )
        states = (
            await conn.execute(
                text(
                    """
                    SELECT fb_ad_id, registration, ftd, confirmed_deposit
                    FROM tracker_click_state
                    WHERE source = 'adsetpro' AND click_id = :click_id
                    """
                ),
                {"click_id": click_id},
            )
        ).all()
    assert task_status == "failed"
    assert event_status == "ambiguous"
    assert states == [(first_fb_ad_id, True, False, False)]


@pytest.mark.asyncio
async def test_unmatched_event_retries_and_attaches_after_ad_appears(
    pg_engine,
    fb_ad_fixture,
    clean_event_projection,
) -> None:
    unknown_fb_ad_id = f"239{uuid.uuid4().int % 10**12:012d}"
    event = _event(f"unmatched-{uuid.uuid4().hex}", unknown_fb_ad_id, "registration")
    await ingest_postback(pg_engine, event)
    first_claim = (await claim_event_tasks(pg_engine))[0]
    first = await process_event_task(pg_engine, claim=first_claim)
    assert first.processed is False
    assert first.attribution_status == "unmatched"

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE fb_ads SET fb_ad_id = :fid WHERE id = :id"),
            {"fid": unknown_fb_ad_id, "id": fb_ad_fixture.ad_id},
        )
        await conn.execute(
            text("UPDATE task_queue SET available_at = now() WHERE id = :id"),
            {"id": first_claim.task_id},
        )

    retry_claim = (await claim_event_tasks(pg_engine))[0]
    retried = await process_event_task(pg_engine, claim=retry_claim)
    assert retried.processed is True
    assert retried.fb_ad_id == unknown_fb_ad_id
    async with pg_engine.connect() as conn:
        matched_ad_id = await conn.scalar(
            text("SELECT ad_id FROM tracker_click_state WHERE source='adsetpro' AND click_id=:c"),
            {"c": event.click_id},
        )
    assert matched_ad_id == fb_ad_fixture.ad_id


@pytest.mark.asyncio
async def test_positive_event_cancels_only_unstarted_automatic_pause(
    pg_engine,
    fb_ad_fixture,
    clean_event_projection,
) -> None:
    fb_ad_id = await _fb_ad_id(pg_engine, fb_ad_fixture.ad_id)
    now = datetime.now(UTC)
    payload = {
        "mutation_kind": "pause_ad",
        "target_id": fb_ad_id,
        "ad_account_id": "123",
        "params": {},
    }
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend)
                VALUES (gen_random_uuid(), :ad_id, :cycle_ts, 10)
                """
            ),
            {"ad_id": fb_ad_fixture.ad_id, "cycle_ts": now},
        )
        for key, requested_by, status_value, external_started in (
            ("auto-open", "bot_auto_stop", "pending", None),
            ("auto-started", "bot_auto_stop", "running", now),
            ("manual-open", "tg:buyer", "pending", None),
        ):
            task_id = await create_task(
                pg_engine,
                task_type="meta_api_mutation",
                idempotency_key=f"{key}-{uuid.uuid4().hex}",
                payload=payload,
                requested_by=requested_by,
                status=status_value,
                connection=conn,
            )
            assert task_id is not None
            if external_started is not None:
                await conn.execute(
                    text(
                        "UPDATE task_queue SET external_started_at = :started_at "
                        "WHERE id = :task_id"
                    ),
                    {"started_at": external_started, "task_id": task_id},
                )

    await ingest_postback(
        pg_engine,
        _event(f"cancel-{uuid.uuid4().hex}", fb_ad_id, "registration"),
    )
    await _drain_one(pg_engine)

    async with pg_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT idempotency_key, status FROM task_queue
                    WHERE idempotency_key LIKE 'auto-open-%'
                       OR idempotency_key LIKE 'auto-started-%'
                       OR idempotency_key LIKE 'manual-open-%'
                    """
                )
            )
        ).all()
    statuses = {
        str(row[0]).split("-", 2)[0] + "-" + str(row[0]).split("-", 2)[1]: row[1] for row in rows
    }
    assert statuses["auto-open"] == "cancelled"
    assert statuses["auto-started"] == "running"
    assert statuses["manual-open"] == "pending"


@pytest.mark.asyncio
async def test_positive_event_cancels_proven_pre_send_retry_before_next_claim(
    pg_engine,
    fb_ad_fixture,
    clean_event_projection,
) -> None:
    """Session rejection and tracker projection cannot miss each other."""
    fb_ad_id = await _fb_ad_id(pg_engine, fb_ad_fixture.ad_id)
    payload = {
        "mutation_kind": "pause_ad",
        "target_id": fb_ad_id,
        "ad_account_id": "123",
        "params": {},
    }
    task_id = await create_task(
        pg_engine,
        task_type="meta_api_mutation",
        idempotency_key=f"auto-open-{uuid.uuid4().hex}",
        payload=payload,
        requested_by="bot_auto_stop",
        lane="money",
    )
    assert task_id is not None
    claim = await claim_browser_ready_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=uuid.uuid4(),
    )
    assert claim.task is not None
    task = claim.task
    assert task.id == task_id
    assert await mark_external_call_started(
        pg_engine,
        task_id=task.id,
        target_lock_key=fb_ad_id,
        lease_owner=task.lease_owner,
        lease_token=task.lease_token,
    )

    await ingest_postback(
        pg_engine,
        _event(f"pre-send-race-{uuid.uuid4().hex}", fb_ad_id, "ftd"),
    )
    await _drain_one(pg_engine)

    async with pg_engine.connect() as conn:
        crossed = (
            await conn.execute(
                text(
                    """
                    SELECT status, external_started_at, cancel_requested_at
                    FROM task_queue WHERE id = :task_id
                    """
                ),
                {"task_id": task.id},
            )
        ).one()
    assert crossed.status == "running"
    assert crossed.external_started_at is not None
    assert crossed.cancel_requested_at is not None

    status = await requeue_proven_not_committed(
        pg_engine,
        task_id=task.id,
        target_lock_key=fb_ad_id,
        error="SessionUnavailableError('browser session unavailable')",
        attempt_count=task.attempt_count,
        max_attempts=task.max_attempts,
        lease_owner=task.lease_owner,
        lease_token=task.lease_token,
        lane=task.lane,
    )
    assert status == "cancelled"

    async with pg_engine.connect() as conn:
        closed = (
            await conn.execute(
                text(
                    """
                    SELECT status, external_started_at, lease_owner,
                           result->>'reason' AS reason
                    FROM task_queue WHERE id = :task_id
                    """
                ),
                {"task_id": task.id},
            )
        ).one()
    assert closed.status == "cancelled"
    assert closed.external_started_at is None
    assert closed.lease_owner is None
    assert closed.reason == "cancelled_after_proven_not_committed"
    assert (
        await claim_browser_ready_task(
            pg_engine,
            task_type="meta_api_mutation",
            lanes=("money",),
            worker_id=uuid.uuid4(),
        )
    ).queue_empty

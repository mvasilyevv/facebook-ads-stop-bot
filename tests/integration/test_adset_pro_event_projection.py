"""Postgres integration for event ordering, retry and cancellation boundary."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.adset_pro.ingest import ingest_postback
from core.adset_pro.processing import claim_event_tasks, process_event_task
from core.adset_pro.schemas import PostbackEvent


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


async def _drain_one(pg_engine, *, auto_cancel_enabled: bool = False):
    claimed = await claim_event_tasks(pg_engine)
    assert len(claimed) == 1
    result = await process_event_task(
        pg_engine,
        task_id=claimed[0],
        auto_cancel_enabled=auto_cancel_enabled,
    )
    assert result.processed is True
    return result


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
        assert result.auto_cancel_shadow_candidate is False

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
async def test_n1_raw_alias_without_task_is_recovered_without_rewriting_raw_event(
    pg_engine,
    fb_ad_fixture,
    clean_event_projection,
) -> None:
    """Roll-forward processes an a3b7 row and preserves it for repeated rollback."""
    fb_ad_id = await _fb_ad_id(pg_engine, fb_ad_fixture.ad_id)
    click_id = f"n1-accept-{uuid.uuid4().hex}"
    now = datetime.now(UTC)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO adsetpro_postback_events
                    (received_at, occurred_at, source, click_id, fb_ad_id, event_type,
                     revenue, currency, raw_json, signature_valid, is_duplicate,
                     attribution_status, attempt_count)
                VALUES
                    (:received_at, :occurred_at, 'adsetpro', :click_id, :fb_ad_id,
                     'accept', 10, 'USD', CAST(:raw_json AS JSONB), TRUE, FALSE,
                     'unmatched', 0)
                """
            ),
            {
                "received_at": now,
                "occurred_at": now,
                "click_id": click_id,
                "fb_ad_id": fb_ad_id,
                "raw_json": json.dumps({"sub8": fb_ad_id, "country": "KE"}),
            },
        )

    claimed = await claim_event_tasks(pg_engine)
    assert len(claimed) == 1
    result = await process_event_task(pg_engine, task_id=claimed[0])
    assert result.processed is True

    async with pg_engine.connect() as conn:
        raw_event_type = await conn.scalar(
            text(
                "SELECT event_type FROM adsetpro_postback_events "
                "WHERE source = 'adsetpro' AND click_id = :click_id"
            ),
            {"click_id": click_id},
        )
        state = (
            await conn.execute(
                text(
                    """
                    SELECT registration, ftd, confirmed_deposit
                    FROM tracker_click_state
                    WHERE source = 'adsetpro' AND click_id = :click_id
                    """
                ),
                {"click_id": click_id},
            )
        ).one()
        requested_by = await conn.scalar(
            text("SELECT requested_by FROM task_queue WHERE id = :task_id"),
            {"task_id": claimed[0]},
        )

    assert raw_event_type == "accept"
    assert state == (False, True, False)
    assert requested_by == "tracker_n1_recovery"


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
    task_id = (await claim_event_tasks(pg_engine))[0]

    conflicted = await process_event_task(pg_engine, task_id=task_id)

    assert conflicted.processed is False
    assert conflicted.attribution_status == "ambiguous"
    async with pg_engine.connect() as conn:
        task_status = await conn.scalar(
            text("SELECT status FROM task_queue WHERE id = :id"), {"id": task_id}
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
    first_task = (await claim_event_tasks(pg_engine))[0]
    first = await process_event_task(pg_engine, task_id=first_task)
    assert first.processed is False
    assert first.attribution_status == "unmatched"

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE fb_ads SET fb_ad_id = :fid WHERE id = :id"),
            {"fid": unknown_fb_ad_id, "id": fb_ad_fixture.ad_id},
        )
        await conn.execute(
            text("UPDATE task_queue SET next_retry_at = now() WHERE id = :id"),
            {"id": first_task},
        )

    retry_task = (await claim_event_tasks(pg_engine))[0]
    retried = await process_event_task(pg_engine, task_id=retry_task)
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
    payload = json.dumps({"mutation_kind": "pause_ad", "target_id": fb_ad_id, "params": {}})
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
            await conn.execute(
                text(
                    """
                    INSERT INTO task_queue
                        (task_type, status, idempotency_key, payload, requested_by,
                         external_started_at)
                    VALUES ('meta_api_mutation', :status, :key, CAST(:payload AS JSONB),
                            :requested_by, :external_started_at)
                    """
                ),
                {
                    "status": status_value,
                    "key": f"{key}-{uuid.uuid4().hex}",
                    "payload": payload,
                    "requested_by": requested_by,
                    "external_started_at": external_started,
                },
            )

    await ingest_postback(
        pg_engine,
        _event(f"cancel-{uuid.uuid4().hex}", fb_ad_id, "registration"),
    )
    result = await _drain_one(pg_engine, auto_cancel_enabled=True)
    assert result.auto_cancel_shadow_candidate is False

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
async def test_shadow_mode_projects_positive_event_without_cancelling_pause(
    pg_engine,
    fb_ad_fixture,
    clean_event_projection,
) -> None:
    fb_ad_id = await _fb_ad_id(pg_engine, fb_ad_fixture.ad_id)
    payload = json.dumps({"mutation_kind": "pause_ad", "target_id": fb_ad_id, "params": {}})
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO task_queue
                    (task_type, status, idempotency_key, payload, requested_by)
                VALUES ('meta_api_mutation', 'pending', :key, CAST(:payload AS JSONB),
                        'bot_auto_stop')
                """
            ),
            {"key": f"auto-shadow-{uuid.uuid4().hex}", "payload": payload},
        )

    await ingest_postback(
        pg_engine,
        _event(f"shadow-{uuid.uuid4().hex}", fb_ad_id, "registration"),
    )
    result = await _drain_one(pg_engine, auto_cancel_enabled=False)

    assert result.auto_cancel_shadow_candidate is True
    assert result.cancelled_task_ids == ()
    async with pg_engine.connect() as conn:
        status_value = await conn.scalar(
            text("SELECT status FROM task_queue WHERE idempotency_key LIKE 'auto-shadow-%'")
        )
    assert status_value == "pending"

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from core.commands import (
    CommandConflictError,
    CommandPreconditionError,
    CommandReceipt,
    CommandService,
)
from core.meta_api.autostop_alert import (
    TERMINAL_UNDELIVERED_INCIDENT_KEY_PREFIX,
    escalate_undelivered_autostop_pauses,
)


async def _seed_ad(engine, *, fb_ad_id: str) -> uuid.UUID:
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    account_id = f"{campaign_id.int % 10**12:012d}"
    fb_campaign_id = f"{campaign_id.int % 10**18:018d}"
    fb_adset_id = f"{adset_id.int % 10**18:018d}"
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:id, :code, :name)"),
            {"id": offer_id, "code": f"CMD_{offer_id.hex[:10]}", "name": "Command CAS"},
        )
        await conn.execute(
            text(
                """
                INSERT INTO fb_campaigns
                    (id, fb_campaign_id, campaign_name, offer_id, ad_account_id)
                VALUES (:id, :fb_campaign_id, :name, :offer_id, :account_id)
                """
            ),
            {
                "id": campaign_id,
                "fb_campaign_id": fb_campaign_id,
                "name": f"CMD_{campaign_id.hex[:10]}",
                "offer_id": offer_id,
                "account_id": account_id,
            },
        )
        await conn.execute(
            text(
                """
                INSERT INTO meta_account_snapshot(
                    account_id, timezone_name, currency, currency_observed_at
                )
                VALUES (:account_id, 'UTC', 'USD', clock_timestamp())
                """
            ),
            {"account_id": account_id},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_adsets (id, campaign_id, fb_adset_id, adset_name) "
                "VALUES (:id, :campaign_id, :fb_adset_id, :name)"
            ),
            {
                "id": adset_id,
                "campaign_id": campaign_id,
                "fb_adset_id": fb_adset_id,
                "name": "Command CAS adset",
            },
        )
        await conn.execute(
            text(
                "INSERT INTO fb_ads "
                "(id, adset_id, fb_ad_id, ad_name, delivery_status) "
                "VALUES (:id, :adset_id, :fb_ad_id, :name, 'ACTIVE')"
            ),
            {"id": ad_id, "adset_id": adset_id, "fb_ad_id": fb_ad_id, "name": "CAS ad"},
        )
        stored = (
            await conn.execute(
                text(
                    """
                    SELECT c.fb_campaign_id, c.ad_account_id, s.fb_adset_id, a.fb_ad_id
                    FROM fb_ads AS a
                    JOIN fb_adsets AS s ON s.id = a.adset_id
                    JOIN fb_campaigns AS c ON c.id = s.campaign_id
                    WHERE a.id = :ad_id
                    """
                ),
                {"ad_id": ad_id},
            )
        ).one()
        assert stored == (fb_campaign_id, account_id, fb_adset_id, fb_ad_id)
        assert all(value.isdigit() for value in stored)
    return offer_id


async def _cleanup(engine, *, fb_ad_id: str, offer_id: uuid.UUID) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM task_queue WHERE payload->>'target_id' = :fb_ad_id"),
            {"fb_ad_id": fb_ad_id},
        )
        await conn.execute(
            text(
                """
                DELETE FROM notification_events
                WHERE incident_id IN (
                  SELECT id
                  FROM incidents
                  WHERE resource_type = 'fb_ad'
                    AND resource_id = :fb_ad_id
                )
                """
            ),
            {"fb_ad_id": fb_ad_id},
        )
        await conn.execute(
            text(
                """
                DELETE FROM incidents
                WHERE resource_type = 'fb_ad'
                  AND resource_id = :fb_ad_id
                """
            ),
            {"fb_ad_id": fb_ad_id},
        )
        await conn.execute(
            text(
                """
                DELETE FROM meta_account_snapshot
                WHERE account_id IN (
                    SELECT ad_account_id
                    FROM fb_campaigns
                    WHERE offer_id = :id
                )
                """
            ),
            {"id": offer_id},
        )
        await conn.execute(
            text("DELETE FROM fb_campaigns WHERE offer_id = :id"),
            {"id": offer_id},
        )
        await conn.execute(text("DELETE FROM offers WHERE id = :id"), {"id": offer_id})


@pytest.mark.asyncio
async def test_post_confirmation_preconditions_reject_changed_ad_state(pg_engine) -> None:
    """A confirm-dialog snapshot is never authority for a later money command."""
    fb_ad_id = f"2399{uuid.uuid4().int % 10**14:014d}"
    offer_id = await _seed_ad(pg_engine, fb_ad_id=fb_ad_id)
    service = CommandService(pg_engine)
    confirmed_as_of = datetime.now(UTC).replace(microsecond=0)
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend)
                    SELECT :metric_id, id, :cycle_ts, 1.00
                    FROM fb_ads
                    WHERE fb_ad_id = :fb_ad_id
                    """
                ),
                {
                    "metric_id": uuid.uuid4(),
                    "cycle_ts": confirmed_as_of,
                    "fb_ad_id": fb_ad_id,
                },
            )
            # Simulate Meta/another operator changing delivery status between
            # the confirmation dialog and the POST.
            await conn.execute(
                text("UPDATE fb_ads SET delivery_status = 'PAUSED' WHERE fb_ad_id = :fb_ad_id"),
                {"fb_ad_id": fb_ad_id},
            )

        with pytest.raises(CommandPreconditionError, match="changed after operator confirmation"):
            await service.enqueue_ad_action(
                action_kind="pause_ad",
                fb_ad_id=fb_ad_id,
                requested_by="operator:web",
                idempotency_key=f"web:status-race:{uuid.uuid4()}",
                expected_delivery_status="ACTIVE",
                expected_as_of=confirmed_as_of,
            )

        async with pg_engine.begin() as conn:
            await conn.execute(
                text("UPDATE fb_ads SET delivery_status = 'ACTIVE' WHERE fb_ad_id = :fb_ad_id"),
                {"fb_ad_id": fb_ad_id},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend)
                    SELECT :metric_id, id, :cycle_ts, 2.00
                    FROM fb_ads
                    WHERE fb_ad_id = :fb_ad_id
                    """
                ),
                {
                    "metric_id": uuid.uuid4(),
                    "cycle_ts": confirmed_as_of + timedelta(seconds=1),
                    "fb_ad_id": fb_ad_id,
                },
            )

        with pytest.raises(CommandPreconditionError, match="changed after operator confirmation"):
            await service.enqueue_ad_action(
                action_kind="pause_ad",
                fb_ad_id=fb_ad_id,
                requested_by="operator:web",
                idempotency_key=f"web:metrics-race:{uuid.uuid4()}",
                expected_delivery_status="ACTIVE",
                expected_as_of=confirmed_as_of,
            )

        async with pg_engine.connect() as conn:
            task_count = await conn.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM task_queue
                    WHERE payload->>'target_id' = :fb_ad_id
                    """
                ),
                {"fb_ad_id": fb_ad_id},
            )
        assert task_count == 0
    finally:
        await _cleanup(pg_engine, fb_ad_id=fb_ad_id, offer_id=offer_id)


@pytest.mark.asyncio
async def test_ui_telegram_and_auto_pause_share_one_active_task(pg_engine) -> None:
    fb_ad_id = f"2398{uuid.uuid4().int % 10**14:014d}"
    offer_id = await _seed_ad(pg_engine, fb_ad_id=fb_ad_id)
    service = CommandService(pg_engine)
    try:
        receipts = await asyncio.gather(
            service.enqueue_ad_action(
                action_kind="pause_ad",
                fb_ad_id=fb_ad_id,
                requested_by="operator:web",
                idempotency_key=f"web:{fb_ad_id}",
            ),
            service.enqueue_ad_action(
                action_kind="pause_ad",
                fb_ad_id=fb_ad_id,
                requested_by="tg:owner",
                idempotency_key=f"telegram:{fb_ad_id}",
            ),
            service.enqueue_ad_action(
                action_kind="pause_ad",
                fb_ad_id=fb_ad_id,
                requested_by="bot_auto_stop",
                idempotency_key=f"auto:pause_ad:{fb_ad_id}:{uuid.uuid4()}",
                max_attempts=15,
            ),
        )

        assert len({receipt.task_id for receipt in receipts}) == 1
        assert sum(receipt.created for receipt in receipts) == 1
        async with pg_engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT count(*) AS count, max(max_attempts) AS max_attempts
                        FROM task_queue
                        WHERE payload->>'target_id' = :fb_ad_id
                          AND payload->>'mutation_kind' = 'pause_ad'
                        """
                    ),
                    {"fb_ad_id": fb_ad_id},
                )
            ).one()
        assert row.count == 1
        # Auto-pause may arrive after web/TG, but its safety retry budget must
        # still strengthen the shared task instead of being silently lost.
        assert row.max_attempts == 15
    finally:
        await _cleanup(pg_engine, fb_ad_id=fb_ad_id, offer_id=offer_id)


@pytest.mark.asyncio
async def test_concurrent_pause_and_activate_cannot_both_remain_active(pg_engine) -> None:
    fb_ad_id = f"2397{uuid.uuid4().int % 10**14:014d}"
    offer_id = await _seed_ad(pg_engine, fb_ad_id=fb_ad_id)
    service = CommandService(pg_engine)
    try:
        outcomes = await asyncio.gather(
            service.enqueue_ad_action(
                action_kind="pause_ad",
                fb_ad_id=fb_ad_id,
                requested_by="bot_auto_stop",
                idempotency_key=f"pause:{fb_ad_id}",
                max_attempts=15,
            ),
            service.enqueue_ad_action(
                action_kind="activate_ad",
                fb_ad_id=fb_ad_id,
                requested_by="operator:web",
                idempotency_key=f"activate:{fb_ad_id}",
            ),
            return_exceptions=True,
        )

        assert sum(isinstance(value, CommandReceipt) for value in outcomes) == 1
        assert (
            sum(
                isinstance(value, (CommandConflictError, CommandPreconditionError))
                for value in outcomes
            )
            == 1
        )
        async with pg_engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT payload->>'mutation_kind' AS action_kind
                        FROM task_queue
                        WHERE payload->>'target_id' = :fb_ad_id
                          AND status IN ('pending','retrying','running')
                        """
                    ),
                    {"fb_ad_id": fb_ad_id},
                )
            ).all()
        assert len(rows) == 1
        assert rows[0].action_kind == "pause_ad"
    finally:
        await _cleanup(pg_engine, fb_ad_id=fb_ad_id, offer_id=offer_id)


@pytest.mark.asyncio
async def test_lost_alias_response_replays_original_task_after_terminal_transition(
    pg_engine,
) -> None:
    """K1 must survive response loss even though active K0 later becomes terminal."""
    fb_ad_id = f"2396{uuid.uuid4().int % 10**14:014d}"
    offer_id = await _seed_ad(pg_engine, fb_ad_id=fb_ad_id)
    service = CommandService(pg_engine)
    key_k0 = f"web:k0:{uuid.uuid4()}"
    key_k1 = f"web:k1:{uuid.uuid4()}"
    try:
        original = await service.enqueue_ad_action(
            action_kind="pause_ad",
            fb_ad_id=fb_ad_id,
            requested_by="operator:web",
            idempotency_key=key_k0,
        )
        lost_response = await service.enqueue_ad_action(
            action_kind="pause_ad",
            fb_ad_id=fb_ad_id,
            requested_by="operator:web",
            idempotency_key=key_k1,
        )
        assert lost_response.task_id == original.task_id
        assert lost_response.created is False

        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    UPDATE task_queue
                    SET status = 'succeeded',
                        result = '{"outcome":"CONFIRMED"}'::jsonb,
                        completed_at = NOW(),
                        updated_at = NOW()
                    WHERE id = :task_id
                    """
                ),
                {"task_id": original.task_id},
            )

        replay = await service.enqueue_ad_action(
            action_kind="pause_ad",
            fb_ad_id=fb_ad_id,
            requested_by="operator:web",
            idempotency_key=key_k1,
        )

        assert replay.task_id == original.task_id
        assert replay.created is False
        assert replay.state == "confirmed"
        async with pg_engine.connect() as conn:
            tasks = (
                await conn.execute(
                    text(
                        """
                        SELECT id, idempotency_key
                        FROM task_queue
                        WHERE payload->>'target_id' = :fb_ad_id
                          AND payload->>'mutation_kind' = 'pause_ad'
                        """
                    ),
                    {"fb_ad_id": fb_ad_id},
                )
            ).all()
            aliases = (
                await conn.execute(
                    text(
                        """
                        SELECT idempotency_key, task_id
                        FROM command_idempotency_receipts
                        WHERE idempotency_key IN (:key_k0, :key_k1)
                        ORDER BY idempotency_key
                        """
                    ),
                    {"key_k0": key_k0, "key_k1": key_k1},
                )
            ).all()
        assert [(row.id, row.idempotency_key) for row in tasks] == [(original.task_id, key_k0)]
        assert {row.idempotency_key for row in aliases} == {key_k0, key_k1}
        assert {row.task_id for row in aliases} == {original.task_id}
    finally:
        await _cleanup(pg_engine, fb_ad_id=fb_ad_id, offer_id=offer_id)


@pytest.mark.asyncio
async def test_fast_confirmed_command_blocks_distinct_keys_until_post_command_evidence(
    pg_engine,
) -> None:
    """Cross-tab keys cannot duplicate a confirmed command against stale catalog data."""
    fb_ad_id = f"2391{uuid.uuid4().int % 10**14:014d}"
    offer_id = await _seed_ad(pg_engine, fb_ad_id=fb_ad_id)
    service = CommandService(pg_engine)
    initial_cycle = datetime.now(UTC) - timedelta(minutes=1)
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend)
                    SELECT :metric_id, id, :cycle_ts, 1.00
                    FROM fb_ads
                    WHERE fb_ad_id = :fb_ad_id
                    """
                ),
                {
                    "metric_id": uuid.uuid4(),
                    "cycle_ts": initial_cycle,
                    "fb_ad_id": fb_ad_id,
                },
            )

        original = await service.enqueue_ad_action(
            action_kind="pause_ad",
            fb_ad_id=fb_ad_id,
            requested_by="operator:web",
            idempotency_key=f"web:tab-a:{uuid.uuid4()}",
            expected_delivery_status="ACTIVE",
            expected_as_of=initial_cycle,
        )
        async with pg_engine.begin() as conn:
            completed_at = (
                await conn.execute(
                    text(
                        """
                        UPDATE task_queue
                        SET status = 'succeeded',
                            result = '{"outcome":"CONFIRMED"}'::jsonb,
                            completed_at = NOW(),
                            updated_at = NOW()
                        WHERE id = :task_id
                        RETURNING completed_at
                        """
                    ),
                    {"task_id": original.task_id},
                )
            ).scalar_one()

        # A second tab can legitimately have minted a different durable key
        # before seeing tab A's localStorage write or terminal task receipt.
        alias = await service.enqueue_ad_action(
            action_kind="pause_ad",
            fb_ad_id=fb_ad_id,
            requested_by="operator:web",
            idempotency_key=f"web:tab-b:{uuid.uuid4()}",
            expected_delivery_status="ACTIVE",
            expected_as_of=initial_cycle,
        )
        assert alias.task_id == original.task_id
        assert alias.created is False
        assert alias.state == "confirmed"

        with pytest.raises(CommandConflictError, match="unresolved pause_ad"):
            await service.enqueue_ad_action(
                action_kind="activate_ad",
                fb_ad_id=fb_ad_id,
                requested_by="operator:web",
                idempotency_key=f"web:opposite-before-evidence:{uuid.uuid4()}",
                expected_delivery_status="ACTIVE",
                expected_as_of=initial_cycle,
            )

        async with pg_engine.connect() as conn:
            stale_task_count = await conn.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM task_queue
                    WHERE payload->>'target_id' = :fb_ad_id
                    """
                ),
                {"fb_ad_id": fb_ad_id},
            )
        assert stale_task_count == 1

        evidence_cycle = completed_at + timedelta(seconds=1)
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend)
                    SELECT :metric_id, id, :cycle_ts, 1.00
                    FROM fb_ads
                    WHERE fb_ad_id = :fb_ad_id
                    """
                ),
                {
                    "metric_id": uuid.uuid4(),
                    "cycle_ts": evidence_cycle,
                    "fb_ad_id": fb_ad_id,
                },
            )

        corrective = await service.enqueue_ad_action(
            action_kind="pause_ad",
            fb_ad_id=fb_ad_id,
            requested_by="operator:web",
            idempotency_key=f"web:fresh-mismatched:{uuid.uuid4()}",
            expected_delivery_status="ACTIVE",
            expected_as_of=evidence_cycle,
        )
        assert corrective.task_id != original.task_id
        assert corrective.created is True

        corrective_alias = await service.enqueue_ad_action(
            action_kind="pause_ad",
            fb_ad_id=fb_ad_id,
            requested_by="bot_auto_stop",
            idempotency_key=f"auto:fresh-mismatched:{uuid.uuid4()}",
            max_attempts=15,
            expected_delivery_status="ACTIVE",
            expected_as_of=evidence_cycle,
        )
        assert corrective_alias.task_id == corrective.task_id
        assert corrective_alias.created is False

        async with pg_engine.connect() as conn:
            counts = (
                await conn.execute(
                    text(
                        """
                        SELECT
                          (
                            SELECT count(*)
                            FROM task_queue
                            WHERE payload->>'target_id' = :fb_ad_id
                          ) AS task_count,
                          (
                            SELECT count(*)
                            FROM incidents
                            WHERE incident_key = :incident_key
                              AND severity = 'critical'
                              AND status = 'open'
                          ) AS incident_count,
                          (
                            SELECT count(*)
                            FROM notification_events AS event
                            JOIN incidents AS incident
                              ON incident.id = event.incident_id
                            WHERE incident.incident_key = :incident_key
                              AND event.event_type = 'worker_meta_status_divergence'
                          ) AS event_count,
                          (
                            SELECT correlation_id
                            FROM incidents
                            WHERE incident_key = :incident_key
                          ) AS incident_correlation,
                          (
                            SELECT event.facts
                            FROM notification_events AS event
                            JOIN incidents AS incident
                              ON incident.id = event.incident_id
                            WHERE incident.incident_key = :incident_key
                              AND event.event_type = 'worker_meta_status_divergence'
                            LIMIT 1
                          ) AS event_facts
                        """
                    ),
                    {
                        "fb_ad_id": fb_ad_id,
                        "incident_key": (f"meta-status-divergence:{fb_ad_id}:{original.task_id}"),
                    },
                )
            ).one()
        assert counts.task_count == 2
        assert counts.incident_count == 1
        assert counts.event_count == 1
        assert counts.incident_correlation == corrective.correlation_id
        # Карточка: что случилось, с каким объявлением и что делает система.
        assert counts.event_facts["title"].startswith("Статус в Facebook разошёлся")
        assert "CAS ad" in counts.event_facts["title"]
        assert f"#{original.task_id}" in counts.event_facts["summary"]
        assert "включённое" in counts.event_facts["summary"]
        assert "ACTIVE" not in counts.event_facts["summary"]
        assert any("Ads Manager" in line for line in counts.event_facts["lines"])
        assert fb_ad_id not in str(counts.event_facts)
    finally:
        await _cleanup(pg_engine, fb_ad_id=fb_ad_id, offer_id=offer_id)


@pytest.mark.asyncio
async def test_matching_post_command_evidence_allows_pause_activate_pause_cycle(
    pg_engine,
) -> None:
    """Only the latest observed command controls the next legitimate action."""
    fb_ad_id = f"2389{uuid.uuid4().int % 10**14:014d}"
    offer_id = await _seed_ad(pg_engine, fb_ad_id=fb_ad_id)
    service = CommandService(pg_engine)
    initial_cycle = datetime.now(UTC) - timedelta(minutes=1)
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend)
                    SELECT :metric_id, id, :cycle_ts, 1.00
                    FROM fb_ads
                    WHERE fb_ad_id = :fb_ad_id
                    """
                ),
                {
                    "metric_id": uuid.uuid4(),
                    "cycle_ts": initial_cycle,
                    "fb_ad_id": fb_ad_id,
                },
            )

        pause = await service.enqueue_ad_action(
            action_kind="pause_ad",
            fb_ad_id=fb_ad_id,
            requested_by="operator:web",
            idempotency_key=f"web:cycle-pause:{uuid.uuid4()}",
            expected_delivery_status="ACTIVE",
            expected_as_of=initial_cycle,
        )
        async with pg_engine.begin() as conn:
            pause_completed_at = (
                await conn.execute(
                    text(
                        """
                        UPDATE task_queue
                        SET status = 'succeeded',
                            result = '{"outcome":"CONFIRMED"}'::jsonb,
                            completed_at = NOW(),
                            updated_at = NOW()
                        WHERE id = :task_id
                        RETURNING completed_at
                        """
                    ),
                    {"task_id": pause.task_id},
                )
            ).scalar_one()
            pause_evidence = pause_completed_at + timedelta(seconds=1)
            await conn.execute(
                text("UPDATE fb_ads SET delivery_status = 'OFF' WHERE fb_ad_id = :fb_ad_id"),
                {"fb_ad_id": fb_ad_id},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend)
                    SELECT :metric_id, id, :cycle_ts, 1.00
                    FROM fb_ads
                    WHERE fb_ad_id = :fb_ad_id
                    """
                ),
                {
                    "metric_id": uuid.uuid4(),
                    "cycle_ts": pause_evidence,
                    "fb_ad_id": fb_ad_id,
                },
            )

        activate = await service.enqueue_ad_action(
            action_kind="activate_ad",
            fb_ad_id=fb_ad_id,
            requested_by="operator:web",
            idempotency_key=f"web:cycle-activate:{uuid.uuid4()}",
            expected_delivery_status="OFF",
            expected_as_of=pause_evidence,
        )
        assert activate.created is True

        async with pg_engine.begin() as conn:
            activate_completed_at = (
                await conn.execute(
                    text(
                        """
                        UPDATE task_queue
                        SET status = 'succeeded',
                            result = '{"outcome":"CONFIRMED"}'::jsonb,
                            completed_at = NOW(),
                            updated_at = NOW()
                        WHERE id = :task_id
                        RETURNING completed_at
                        """
                    ),
                    {"task_id": activate.task_id},
                )
            ).scalar_one()
            activate_evidence = activate_completed_at + timedelta(seconds=1)
            await conn.execute(
                text("UPDATE fb_ads SET delivery_status = 'ACTIVE' WHERE fb_ad_id = :fb_ad_id"),
                {"fb_ad_id": fb_ad_id},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend)
                    SELECT :metric_id, id, :cycle_ts, 1.00
                    FROM fb_ads
                    WHERE fb_ad_id = :fb_ad_id
                    """
                ),
                {
                    "metric_id": uuid.uuid4(),
                    "cycle_ts": activate_evidence,
                    "fb_ad_id": fb_ad_id,
                },
            )

        next_pause = await service.enqueue_ad_action(
            action_kind="pause_ad",
            fb_ad_id=fb_ad_id,
            requested_by="operator:web",
            idempotency_key=f"web:cycle-next-pause:{uuid.uuid4()}",
            expected_delivery_status="ACTIVE",
            expected_as_of=activate_evidence,
        )
        assert next_pause.created is True
        assert len({pause.task_id, activate.task_id, next_pause.task_id}) == 3

        async with pg_engine.connect() as conn:
            incident_count = await conn.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM incidents
                    WHERE resource_type = 'fb_ad'
                      AND resource_id = :fb_ad_id
                    """
                ),
                {"fb_ad_id": fb_ad_id},
            )
        assert incident_count == 0
    finally:
        await _cleanup(pg_engine, fb_ad_id=fb_ad_id, offer_id=offer_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action_kind", "delivery_status"),
    [
        ("activate_ad", "ACTIVE"),
        ("pause_ad", "OFF"),
        ("pause_ad", "ARCHIVED"),
        ("activate_ad", "ARCHIVED"),
    ],
)
async def test_direct_noop_or_unknown_delivery_command_is_rejected(
    pg_engine,
    action_kind,
    delivery_status,
) -> None:
    fb_ad_id = f"2388{uuid.uuid4().int % 10**14:014d}"
    offer_id = await _seed_ad(pg_engine, fb_ad_id=fb_ad_id)
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE fb_ads SET delivery_status = :delivery_status "
                    "WHERE fb_ad_id = :fb_ad_id"
                ),
                {
                    "delivery_status": delivery_status,
                    "fb_ad_id": fb_ad_id,
                },
            )

        with pytest.raises(CommandPreconditionError, match="is not allowed"):
            await CommandService(pg_engine).enqueue_ad_action(
                action_kind=action_kind,
                fb_ad_id=fb_ad_id,
                requested_by="operator:direct-test",
                idempotency_key=f"direct:{action_kind}:{uuid.uuid4()}",
            )

        async with pg_engine.connect() as conn:
            task_count = await conn.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM task_queue
                    WHERE payload->>'target_id' = :fb_ad_id
                    """
                ),
                {"fb_ad_id": fb_ad_id},
            )
        assert task_count == 0
    finally:
        await _cleanup(pg_engine, fb_ad_id=fb_ad_id, offer_id=offer_id)


@pytest.mark.asyncio
async def test_terminal_unknown_blocks_opposite_and_reuses_same_action(pg_engine) -> None:
    """An ambiguous money result remains a target barrier after task termination."""
    fb_ad_id = f"2390{uuid.uuid4().int % 10**14:014d}"
    offer_id = await _seed_ad(pg_engine, fb_ad_id=fb_ad_id)
    service = CommandService(pg_engine)
    original_key = f"web:unknown-original:{uuid.uuid4()}"
    alias_key = f"web:unknown-alias:{uuid.uuid4()}"
    try:
        original = await service.enqueue_ad_action(
            action_kind="pause_ad",
            fb_ad_id=fb_ad_id,
            requested_by="operator:web",
            idempotency_key=original_key,
        )
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    UPDATE task_queue
                    SET status = 'failed',
                        result = CAST(:result AS JSONB),
                        completed_at = NOW(),
                        updated_at = NOW()
                    WHERE id = :task_id
                    """
                ),
                {
                    "task_id": original.task_id,
                    "result": (
                        '{"outcome":"UNKNOWN","reconcile_required":false,'
                        '"reason":"reconciliation_exhausted"}'
                    ),
                },
            )

        alias = await service.enqueue_ad_action(
            action_kind="pause_ad",
            fb_ad_id=fb_ad_id,
            requested_by="tg:owner",
            idempotency_key=alias_key,
        )

        assert alias.task_id == original.task_id
        assert alias.created is False
        assert alias.state == "unknown"
        with pytest.raises(CommandConflictError, match="unresolved pause_ad"):
            await service.enqueue_ad_action(
                action_kind="activate_ad",
                fb_ad_id=fb_ad_id,
                requested_by="operator:web",
                idempotency_key=f"web:unknown-opposite:{uuid.uuid4()}",
            )

        async with pg_engine.connect() as conn:
            task_count = await conn.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM task_queue
                    WHERE payload->>'target_id' = :fb_ad_id
                    """
                ),
                {"fb_ad_id": fb_ad_id},
            )
            aliases = (
                await conn.execute(
                    text(
                        """
                        SELECT idempotency_key, task_id
                        FROM command_idempotency_receipts
                        WHERE idempotency_key IN (:original_key, :alias_key)
                        """
                    ),
                    {"original_key": original_key, "alias_key": alias_key},
                )
            ).all()
        assert task_count == 1
        assert {row.idempotency_key for row in aliases} == {original_key, alias_key}
        assert {row.task_id for row in aliases} == {original.task_id}
    finally:
        await _cleanup(pg_engine, fb_ad_id=fb_ad_id, offer_id=offer_id)


@pytest.mark.asyncio
async def test_orphan_queue_key_fails_closed_before_active_reuse(pg_engine) -> None:
    target_a = f"2395{uuid.uuid4().int % 10**14:014d}"
    target_b = f"2394{uuid.uuid4().int % 10**14:014d}"
    offer_a = await _seed_ad(pg_engine, fb_ad_id=target_a)
    offer_b = await _seed_ad(pg_engine, fb_ad_id=target_b)
    service = CommandService(pg_engine)
    conflict_key = f"web:conflict:{uuid.uuid4()}"
    try:
        active = await service.enqueue_ad_action(
            action_kind="pause_ad",
            fb_ad_id=target_a,
            requested_by="operator:web",
            idempotency_key=f"web:active:{uuid.uuid4()}",
        )
        other = await service.enqueue_ad_action(
            action_kind="pause_ad",
            fb_ad_id=target_b,
            requested_by="operator:web",
            idempotency_key=conflict_key,
        )
        # Simulate a corrupt orphan queue key.  The clean runtime must reject it
        # instead of adopting the row or masking it through active reuse.
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM command_idempotency_receipts WHERE idempotency_key = :conflict_key"
                ),
                {"conflict_key": conflict_key},
            )

        with pytest.raises(CommandConflictError, match="outside the command receipt ledger"):
            await service.enqueue_ad_action(
                action_kind="pause_ad",
                fb_ad_id=target_a,
                requested_by="operator:web",
                idempotency_key=conflict_key,
            )

        async with pg_engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT id, payload->>'target_id' AS target_id
                        FROM task_queue
                        WHERE id IN (:active_id, :other_id)
                        ORDER BY id
                        """
                    ),
                    {"active_id": active.task_id, "other_id": other.task_id},
                )
            ).all()
        assert {row.target_id for row in rows} == {target_a, target_b}
        assert len(rows) == 2
    finally:
        await _cleanup(pg_engine, fb_ad_id=target_a, offer_id=offer_a)
        await _cleanup(pg_engine, fb_ad_id=target_b, offer_id=offer_b)


@pytest.mark.asyncio
async def test_concurrent_same_key_different_targets_commits_one_binding(pg_engine) -> None:
    target_a = f"2393{uuid.uuid4().int % 10**14:014d}"
    target_b = f"2392{uuid.uuid4().int % 10**14:014d}"
    offer_a = await _seed_ad(pg_engine, fb_ad_id=target_a)
    offer_b = await _seed_ad(pg_engine, fb_ad_id=target_b)
    service = CommandService(pg_engine)
    shared_key = f"web:race:{uuid.uuid4()}"
    try:
        outcomes = await asyncio.gather(
            service.enqueue_ad_action(
                action_kind="pause_ad",
                fb_ad_id=target_a,
                requested_by="operator:web",
                idempotency_key=shared_key,
            ),
            service.enqueue_ad_action(
                action_kind="pause_ad",
                fb_ad_id=target_b,
                requested_by="operator:web",
                idempotency_key=shared_key,
            ),
            return_exceptions=True,
        )

        assert sum(isinstance(value, CommandReceipt) for value in outcomes) == 1
        assert sum(isinstance(value, CommandConflictError) for value in outcomes) == 1
        async with pg_engine.connect() as conn:
            binding = (
                await conn.execute(
                    text(
                        """
                        SELECT receipt.task_id, receipt.target_id,
                               task.idempotency_key AS queue_key
                        FROM command_idempotency_receipts AS receipt
                        JOIN task_queue AS task ON task.id = receipt.task_id
                        WHERE receipt.idempotency_key = :shared_key
                        """
                    ),
                    {"shared_key": shared_key},
                )
            ).one()
            task_count = await conn.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM task_queue
                    WHERE payload->>'target_id' IN (:target_a, :target_b)
                      AND payload->>'mutation_kind' = 'pause_ad'
                    """
                ),
                {"target_a": target_a, "target_b": target_b},
            )
        assert binding.target_id in {target_a, target_b}
        assert binding.queue_key == shared_key
        assert task_count == 1
    finally:
        await _cleanup(pg_engine, fb_ad_id=target_a, offer_id=offer_a)
        await _cleanup(pg_engine, fb_ad_id=target_b, offer_id=offer_b)


@pytest.mark.asyncio
async def test_concurrent_autostop_scans_replace_rejected_generation_once(
    pg_engine,
) -> None:
    fb_ad_id = f"2390{uuid.uuid4().int % 10**14:014d}"
    offer_id = await _seed_ad(pg_engine, fb_ad_id=fb_ad_id)
    service = CommandService(pg_engine)
    command_key = f"auto:pause_ad:{fb_ad_id}:{uuid.uuid4()}"
    incident_id = uuid.uuid4()
    incident_key = f"ad:{fb_ad_id}:{uuid.uuid4()}"
    terminal_key = f"{TERMINAL_UNDELIVERED_INCIDENT_KEY_PREFIX}{fb_ad_id}"
    try:
        original = await service.enqueue_ad_action(
            action_kind="pause_ad",
            fb_ad_id=fb_ad_id,
            requested_by="bot_auto_stop",
            idempotency_key=command_key,
            max_attempts=15,
        )
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO incidents
                        (id, incident_key, generation, resource_type, resource_id,
                         severity, status, title, correlation_id, resolved_at)
                    VALUES
                        (:id, :incident_key, 1, 'ad', :resource_id,
                         'critical', 'failed', 'Auto-stop rejected',
                         :correlation_id, NOW())
                    """
                ),
                {
                    "id": incident_id,
                    "incident_key": incident_key,
                    "resource_id": fb_ad_id,
                    "correlation_id": original.correlation_id,
                },
            )
            await conn.execute(
                text(
                    """
                    UPDATE task_queue
                    SET status = 'failed',
                        result = '{"outcome":"REJECTED"}'::jsonb,
                        completed_at = NOW(),
                        created_at = NOW() - INTERVAL '15 minutes',
                        updated_at = NOW()
                    WHERE id = :task_id
                    """
                ),
                {"task_id": original.task_id},
            )

        assert (
            await escalate_undelivered_autostop_pauses(
                pg_engine,
                stuck_after_seconds=600,
            )
            == 1
        )
        assert (
            await escalate_undelivered_autostop_pauses(
                pg_engine,
                stuck_after_seconds=600,
            )
            == 0
        )

        scans = await asyncio.gather(
            service.enqueue_ad_action(
                action_kind="pause_ad",
                fb_ad_id=fb_ad_id,
                requested_by="bot_auto_stop",
                idempotency_key=command_key,
                max_attempts=15,
            ),
            service.enqueue_ad_action(
                action_kind="pause_ad",
                fb_ad_id=fb_ad_id,
                requested_by="bot_auto_stop",
                idempotency_key=command_key,
                max_attempts=15,
            ),
        )

        assert scans[0].task_id == scans[1].task_id
        assert scans[0].task_id != original.task_id
        assert {scan.created for scan in scans} == {False, True}
        async with pg_engine.connect() as conn:
            tasks = (
                await conn.execute(
                    text(
                        """
                        SELECT id, status, idempotency_key, correlation_id
                        FROM task_queue
                        WHERE payload->>'target_id' = :fb_ad_id
                          AND payload->>'mutation_kind' = 'pause_ad'
                        ORDER BY id
                        """
                    ),
                    {"fb_ad_id": fb_ad_id},
                )
            ).all()
            incidents = (
                await conn.execute(
                    text(
                        """
                        SELECT incident_key, status, resolved_at
                        FROM incidents
                        WHERE incident_key IN (:incident_key, :terminal_key)
                        ORDER BY incident_key
                        """
                    ),
                    {"incident_key": incident_key, "terminal_key": terminal_key},
                )
            ).all()

        assert len(tasks) == 2
        assert [(row.id, row.status) for row in tasks] == [
            (original.task_id, "failed"),
            (scans[0].task_id, "pending"),
        ]
        assert tasks[1].idempotency_key.startswith("auto:pause_ad:retry:")
        assert {row.correlation_id for row in tasks} == {original.correlation_id}
        assert [(row.incident_key, row.status, row.resolved_at) for row in incidents] == [
            (incident_key, "open", None),
            (terminal_key, "open", None),
        ]
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM incidents WHERE incident_key IN (:incident_key, :terminal_key)"),
                {"incident_key": incident_key, "terminal_key": terminal_key},
            )
        await _cleanup(pg_engine, fb_ad_id=fb_ad_id, offer_id=offer_id)

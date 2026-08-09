"""Recurring worker incidents and rolling notification dedupe in PostgreSQL."""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

import core.meta_api.autostop_alert as autostop_alert
import core.telegram.worker_notify as worker_notify
from core.meta_api.autostop_alert import (
    AUTOSTOP_CHANNEL_INCIDENT_KEY,
    UNDELIVERED_INCIDENT_KEY_PREFIX,
    escalate_undelivered_autostop_pauses,
    maybe_alert_autostop_channel_down,
)
from core.meta_api.errors import TemporaryError
from core.tasks.queue import mark_succeeded
from core.telegram.gateway import telegram_credential_fingerprint
from core.telegram.notifications import (
    claim_notification_delivery as _claim_notification_delivery,
)
from core.telegram.notifications import (
    enqueue_notification_in_rolling_window,
    mark_delivery_sent,
)
from core.telegram.notifications import (
    mark_delivery_external_started as _mark_delivery_external_started,
)
from core.telegram.schemas import NotificationCardFacts, NotificationEventSpec
from core.telegram.worker_notify import (
    notify_recipients,
    notify_recurring_incident,
    resolve_recurring_incident,
)

pytestmark = pytest.mark.usefixtures("authoritative_telegram_config")

_BOT_GENERATION = 4242
_BOT_FINGERPRINT = telegram_credential_fingerprint("integration-telegram-authority-token")


async def claim_notification_delivery(engine, **kwargs):
    return await _claim_notification_delivery(
        engine,
        gateway_generation=_BOT_GENERATION,
        credential_fingerprint=_BOT_FINGERPRINT,
        **kwargs,
    )


async def mark_delivery_external_started(engine, **kwargs):
    return await _mark_delivery_external_started(
        engine,
        gateway_generation=_BOT_GENERATION,
        credential_fingerprint=_BOT_FINGERPRINT,
        **kwargs,
    )


@pytest_asyncio.fixture
async def clean_worker_incidents(pg_engine):
    async def _clean() -> None:
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM telegram_action_tokens"))
            await conn.execute(text("DELETE FROM telegram_message_slots"))
            await conn.execute(text("DELETE FROM notification_deliveries"))
            await conn.execute(text("DELETE FROM notification_events"))
            await conn.execute(text("DELETE FROM task_queue"))
            await conn.execute(text("DELETE FROM incidents"))
            await conn.execute(text("DELETE FROM telegram_recipient_preferences"))
            await conn.execute(text("DELETE FROM telegram_recipients"))

    await _clean()
    async with pg_engine.begin() as conn:
        recipient_id = (
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_recipients
                        (chat_id, telegram_user_id, role)
                    VALUES (880011, 880012, 'owner')
                    RETURNING id
                    """
                )
            )
        ).scalar_one()
        await conn.execute(
            text(
                """
                INSERT INTO telegram_recipient_preferences
                    (recipient_id, min_severity)
                VALUES (:recipient_id, 'ok')
                """
            ),
            {"recipient_id": recipient_id},
        )
    yield
    await _clean()


@pytest.mark.asyncio
async def test_recurring_incident_uses_one_generation_and_editable_slot(
    pg_engine,
    clean_worker_incidents,
) -> None:
    key = f"test:observer-degraded:{uuid.uuid4()}"
    assert await notify_recurring_incident(
        pg_engine,
        incident_key=key,
        audience="all",
        event_type="observer_degraded",
        severity="critical",
        title="Observer degraded",
        summary="Scan unavailable",
    )

    open_claim = await claim_notification_delivery(pg_engine, worker_id="incident-open")
    assert open_claim is not None
    assert open_claim.incident_id is not None
    assert open_claim.incident_generation == 1
    assert open_claim.slot_message_id is None
    assert (
        await mark_delivery_external_started(
            pg_engine,
            claim=open_claim,
            operation_kind="send",
        )
        == "ready"
    )
    assert await mark_delivery_sent(
        pg_engine,
        claim=open_claim,
        message_id=9001,
        render_hash=b"o" * 32,
    )

    # A material repeat refreshes the same generation through an editable
    # content-addressed snapshot event.
    assert await notify_recurring_incident(
        pg_engine,
        incident_key=key,
        audience="all",
        event_type="observer_degraded",
        severity="critical",
        title="Observer degraded",
        summary="Still unavailable",
    )
    async with pg_engine.connect() as conn:
        assert (
            await conn.scalar(
                text("SELECT COUNT(*) FROM incidents WHERE incident_key = :key"),
                {"key": key},
            )
            == 1
        )
        assert (
            await conn.scalar(
                text(
                    """
                    SELECT COUNT(*) FROM notification_events
                    WHERE incident_id = :incident_id
                    """
                ),
                {"incident_id": open_claim.incident_id},
            )
            == 2
        )

    revision_claim = await claim_notification_delivery(pg_engine, worker_id="incident-revision")
    assert revision_claim is not None
    assert revision_claim.incident_id == open_claim.incident_id
    assert revision_claim.event.event_type == "incident_snapshot_updated"
    assert revision_claim.event.facts.summary == "Still unavailable"
    assert revision_claim.slot_message_id == 9001
    assert (
        await mark_delivery_external_started(
            pg_engine,
            claim=revision_claim,
            operation_kind="edit",
        )
        == "ready"
    )
    assert await mark_delivery_sent(
        pg_engine,
        claim=revision_claim,
        message_id=9001,
        render_hash=b"u" * 32,
    )
    assert await resolve_recurring_incident(
        pg_engine,
        incident_key=key,
        audience="all",
        summary="Scan restored",
    )
    recovery_claim = await claim_notification_delivery(pg_engine, worker_id="incident-recovery")
    assert recovery_claim is not None
    assert recovery_claim.incident_id == open_claim.incident_id
    assert recovery_claim.incident_generation == 1
    assert recovery_claim.incident_status == "resolved"
    assert recovery_claim.slot_message_id == 9001
    assert (
        await mark_delivery_external_started(
            pg_engine,
            claim=recovery_claim,
            operation_kind="edit",
        )
        == "ready"
    )
    assert await mark_delivery_sent(
        pg_engine,
        claim=recovery_claim,
        message_id=9001,
        render_hash=b"r" * 32,
    )

    async with pg_engine.connect() as conn:
        slot = (
            await conn.execute(
                text(
                    """
                    SELECT COUNT(*) AS count, MIN(message_id) AS message_id,
                           MIN(state) AS state
                    FROM telegram_message_slots
                    WHERE incident_id = :incident_id
                    """
                ),
                {"incident_id": open_claim.incident_id},
            )
        ).one()
    assert slot.count == 1
    assert slot.message_id == 9001
    assert slot.state == "resolved"


@pytest.mark.asyncio
async def test_reopened_incident_gets_next_generation(
    pg_engine,
    clean_worker_incidents,
) -> None:
    key = f"test:health:{uuid.uuid4()}"
    kwargs = {
        "incident_key": key,
        "audience": "owners",
        "event_type": "health_source_down",
        "severity": "critical",
        "title": "Source down",
    }
    assert await notify_recurring_incident(pg_engine, **kwargs)
    assert await resolve_recurring_incident(
        pg_engine,
        incident_key=key,
        audience="owners",
    )
    assert await notify_recurring_incident(pg_engine, **kwargs)

    async with pg_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT generation, status
                    FROM incidents
                    WHERE incident_key = :key
                    ORDER BY generation
                    """
                ),
                {"key": key},
            )
        ).all()
    assert rows == [(1, "resolved"), (2, "open")]


@pytest.mark.asyncio
async def test_identical_active_snapshot_catches_up_new_recipient_without_new_event(
    pg_engine,
    clean_worker_incidents,
) -> None:
    key = f"test:catch-up:{uuid.uuid4()}"
    kwargs = {
        "incident_key": key,
        "audience": "all",
        "event_type": "observer_degraded",
        "severity": "critical",
        "title": "Observer degraded",
        "summary": "Scan unavailable",
    }
    assert await notify_recurring_incident(pg_engine, **kwargs)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_recipients
                    (chat_id, telegram_user_id, role)
                VALUES (880021, 880022, 'recipient')
                """
            )
        )
    assert await notify_recurring_incident(pg_engine, **kwargs)

    async with pg_engine.connect() as conn:
        event_count = await conn.scalar(
            text(
                """
                SELECT COUNT(*)
                FROM notification_events AS event
                JOIN incidents AS incident ON incident.id = event.incident_id
                WHERE incident.incident_key = :key
                """
            ),
            {"key": key},
        )
        delivery_count = await conn.scalar(
            text(
                """
                SELECT COUNT(*)
                FROM notification_deliveries AS delivery
                JOIN notification_events AS event ON event.id = delivery.event_id
                JOIN incidents AS incident ON incident.id = event.incident_id
                WHERE incident.incident_key = :key
                """
            ),
            {"key": key},
        )
    assert event_count == 1
    assert delivery_count == 2


@pytest.mark.asyncio
async def test_rolling_window_has_no_epoch_boundary_duplicate(
    pg_engine,
    clean_worker_incidents,
) -> None:
    logical = f"test:rolling:{uuid.uuid4()}"
    kwargs = {
        "event_type": "rolling_warning",
        "severity": "warning",
        "title": "Repeated warning",
        "dedupe_key": logical,
        "dedupe_ttl_seconds": 300,
    }
    assert await notify_recipients(pg_engine, **kwargs)
    assert await notify_recipients(pg_engine, **kwargs)

    async with pg_engine.connect() as conn:
        assert await conn.scalar(text("SELECT COUNT(*) FROM notification_events")) == 1

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE notification_events
                SET created_at = NOW() - INTERVAL '301 seconds'
                """
            )
        )
    assert await notify_recipients(pg_engine, **kwargs)
    async with pg_engine.connect() as conn:
        assert await conn.scalar(text("SELECT COUNT(*) FROM notification_events")) == 2


@pytest.mark.asyncio
async def test_rolling_window_keeps_distinct_long_keys(
    pg_engine,
    clean_worker_incidents,
) -> None:
    shared_prefix = "long:" + ("x" * 185)
    specs = [
        NotificationEventSpec(
            event_type="long_key_warning",
            severity="warning",
            audience="all",
            facts=NotificationCardFacts(title=f"Long key {suffix}"),
            dedupe_key=f"{shared_prefix}{suffix}",
        )
        for suffix in ("a", "b")
    ]

    first = await enqueue_notification_in_rolling_window(
        pg_engine,
        specs[0],
        window_seconds=300,
    )
    second = await enqueue_notification_in_rolling_window(
        pg_engine,
        specs[1],
        window_seconds=300,
    )

    assert first.was_created is True
    assert second.was_created is True
    assert first.event_id != second.event_id
    async with pg_engine.connect() as conn:
        assert await conn.scalar(text("SELECT COUNT(*) FROM notification_events")) == 2


@pytest.mark.asyncio
async def test_concurrent_recurring_detections_create_one_incident_event(
    pg_engine,
    clean_worker_incidents,
) -> None:
    key = f"test:concurrent:{uuid.uuid4()}"

    async def _detect() -> bool:
        return await notify_recurring_incident(
            pg_engine,
            incident_key=key,
            audience="owners",
            event_type="health_concurrent",
            severity="critical",
            title="Concurrent outage",
        )

    assert all(await asyncio.gather(*(_detect() for _ in range(10))))
    async with pg_engine.connect() as conn:
        incident_count = await conn.scalar(
            text("SELECT COUNT(*) FROM incidents WHERE incident_key = :key"),
            {"key": key},
        )
        event_count = await conn.scalar(
            text(
                """
                SELECT COUNT(*)
                FROM notification_events event
                JOIN incidents incident ON incident.id = event.incident_id
                WHERE incident.incident_key = :key
                """
            ),
            {"key": key},
        )
        delivery_count = await conn.scalar(
            text(
                """
                SELECT COUNT(*)
                FROM notification_deliveries delivery
                JOIN notification_events event ON event.id = delivery.event_id
                JOIN incidents incident ON incident.id = event.incident_id
                WHERE incident.incident_key = :key
                """
            ),
            {"key": key},
        )
    assert incident_count == 1
    assert event_count == 1
    assert delivery_count == 1


@pytest.mark.asyncio
async def test_concurrent_autostop_channel_failures_create_one_incident_generation(
    pg_engine,
    clean_worker_incidents,
) -> None:
    async def _detect() -> bool:
        return await maybe_alert_autostop_channel_down(
            exc=TemporaryError("Failed to fetch", code=-2),
            fb_ad_id="120246662749510044",
            engine=pg_engine,
        )

    assert all(await asyncio.gather(*(_detect() for _ in range(10))))
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT COUNT(*) AS count, MIN(generation) AS generation,
                           MIN(resource_type) AS resource_type,
                           MIN(resource_id) AS resource_id
                    FROM incidents
                    WHERE incident_key = :key
                    """
                ),
                {"key": AUTOSTOP_CHANNEL_INCIDENT_KEY},
            )
        ).one()
        event_count = await conn.scalar(
            text(
                """
                SELECT COUNT(*)
                FROM notification_events event
                JOIN incidents incident ON incident.id = event.incident_id
                WHERE incident.incident_key = :key
                """
            ),
            {"key": AUTOSTOP_CHANNEL_INCIDENT_KEY},
        )
        delivery_count = await conn.scalar(
            text(
                """
                SELECT COUNT(*)
                FROM notification_deliveries delivery
                JOIN notification_events event ON event.id = delivery.event_id
                JOIN incidents incident ON incident.id = event.incident_id
                WHERE incident.incident_key = :key
                """
            ),
            {"key": AUTOSTOP_CHANNEL_INCIDENT_KEY},
        )

    assert row == (1, 1, "meta_channel", "auto_stop")
    assert event_count == 1
    assert delivery_count == 1


@pytest.mark.asyncio
async def test_per_ad_autostop_incident_repeats_then_resolves_only_on_confirmed_fact(
    pg_engine,
    clean_worker_incidents,
) -> None:
    fb_ad_id = "120246662749510044"
    lease_owner = uuid.uuid4()
    payload = json.dumps(
        {
            "mutation_kind": "pause_ad",
            "target_id": fb_ad_id,
            "params": {},
            "ad_account_id": "123",
        }
    )
    async with pg_engine.begin() as conn:
        task_id = (
            await conn.execute(
                text(
                    """
                INSERT INTO task_queue
                    (task_type, status, idempotency_key, payload, requested_by,
                     lane, attempt_count, max_attempts, last_error, created_at, updated_at,
                     lease_owner, lease_token, lease_expires_at)
                VALUES
                    ('meta_api_mutation', 'running', :key, CAST(:payload AS JSONB),
                     'bot_auto_stop', 'money', 5, 72, 'Failed to fetch',
                     NOW() - INTERVAL '15 minutes', NOW(), :lease_owner, 7,
                     NOW() + INTERVAL '30 seconds')
                RETURNING id
                """
                ),
                {
                    "key": f"test:autostop:{uuid.uuid4()}",
                    "payload": payload,
                    "lease_owner": lease_owner,
                },
            )
        ).scalar_one()

    assert await maybe_alert_autostop_channel_down(
        exc=TemporaryError("Failed to fetch", code=-2),
        fb_ad_id=fb_ad_id,
        engine=pg_engine,
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
        == 1
    )

    per_ad_key = f"{UNDELIVERED_INCIDENT_KEY_PREFIX}{fb_ad_id}"
    async with pg_engine.connect() as conn:
        open_rows = (
            await conn.execute(
                text(
                    """
                    SELECT incident_key, generation, status, resource_type, resource_id
                    FROM incidents
                    WHERE incident_key IN (:channel_key, :per_ad_key)
                    ORDER BY incident_key
                    """
                ),
                {
                    "channel_key": AUTOSTOP_CHANNEL_INCIDENT_KEY,
                    "per_ad_key": per_ad_key,
                },
            )
        ).all()
        per_ad_event_count = await conn.scalar(
            text(
                """
                SELECT COUNT(*)
                FROM notification_events event
                JOIN incidents incident ON incident.id = event.incident_id
                WHERE incident.incident_key = :key
                """
            ),
            {"key": per_ad_key},
        )

    assert open_rows == [
        (AUTOSTOP_CHANNEL_INCIDENT_KEY, 1, "open", "meta_channel", "auto_stop"),
        (per_ad_key, 1, "open", "ad", fb_ad_id),
    ]
    assert per_ad_event_count == 1

    assert await mark_succeeded(
        pg_engine,
        task_id=task_id,
        result={"outcome": "CONFIRMED", "status": "PAUSED"},
        lease_owner=lease_owner,
        lease_token=7,
    )

    async with pg_engine.connect() as conn:
        resolved = (
            await conn.execute(
                text(
                    """
                    SELECT incident_key, status
                    FROM incidents
                    WHERE incident_key IN (:channel_key, :per_ad_key)
                    ORDER BY incident_key
                    """
                ),
                {
                    "channel_key": AUTOSTOP_CHANNEL_INCIDENT_KEY,
                    "per_ad_key": per_ad_key,
                },
            )
        ).all()
        recovery_count = await conn.scalar(
            text(
                """
                SELECT COUNT(*)
                FROM notification_events event
                JOIN incidents incident ON incident.id = event.incident_id
                WHERE incident.incident_key IN (:channel_key, :per_ad_key)
                  AND event.event_type = 'incident_recovered'
                """
            ),
            {
                "channel_key": AUTOSTOP_CHANNEL_INCIDENT_KEY,
                "per_ad_key": per_ad_key,
            },
        )

    assert resolved == [
        (AUTOSTOP_CHANNEL_INCIDENT_KEY, "open"),
        (per_ad_key, "resolved"),
    ]
    assert recovery_count == 1


@pytest.mark.asyncio
async def test_escalator_rechecks_after_candidate_task_has_succeeded(
    pg_engine,
    clean_worker_incidents,
    monkeypatch,
) -> None:
    fb_ad_id = "120246662749510077"
    lease_owner = uuid.uuid4()
    payload = json.dumps(
        {
            "mutation_kind": "pause_ad",
            "target_id": fb_ad_id,
            "params": {},
            "ad_account_id": "123",
        }
    )
    async with pg_engine.begin() as conn:
        task_id = await conn.scalar(
            text(
                """
                INSERT INTO task_queue
                    (task_type, status, idempotency_key, payload, requested_by,
                     lane, created_at, lease_owner, lease_token, lease_expires_at)
                VALUES
                    ('meta_api_mutation', 'running', :key, CAST(:payload AS JSONB),
                     'bot_auto_stop', 'money', NOW() - INTERVAL '15 minutes',
                     :lease_owner, 17, NOW() + INTERVAL '30 seconds')
                RETURNING id
                """
            ),
            {
                "key": f"test:autostop:race:{uuid.uuid4()}",
                "payload": payload,
                "lease_owner": lease_owner,
            },
        )

    selected = asyncio.Event()
    continue_recheck = asyncio.Event()
    original_find = autostop_alert._find_undelivered_candidate_ids

    async def _find_then_pause(*args, **kwargs):
        ids = await original_find(*args, **kwargs)
        assert task_id in ids
        selected.set()
        await continue_recheck.wait()
        return ids

    monkeypatch.setattr(
        autostop_alert,
        "_find_undelivered_candidate_ids",
        _find_then_pause,
    )
    escalation = asyncio.create_task(
        escalate_undelivered_autostop_pauses(pg_engine, stuck_after_seconds=600)
    )
    await asyncio.wait_for(selected.wait(), timeout=2)
    assert await mark_succeeded(
        pg_engine,
        task_id=task_id,
        result={"outcome": "CONFIRMED", "status": "PAUSED"},
        lease_owner=lease_owner,
        lease_token=17,
    )
    continue_recheck.set()
    assert await asyncio.wait_for(escalation, timeout=2) == 0

    async with pg_engine.connect() as conn:
        assert (
            await conn.scalar(
                text("SELECT COUNT(*) FROM incidents WHERE incident_key = :key"),
                {"key": f"{UNDELIVERED_INCIDENT_KEY_PREFIX}{fb_ad_id}"},
            )
            == 0
        )


@pytest.mark.asyncio
async def test_autostop_recovery_projection_rolls_back_task_on_incident_failure(
    pg_engine,
    clean_worker_incidents,
    monkeypatch,
) -> None:
    fb_ad_id = "120246662749510099"
    per_ad_key = f"{UNDELIVERED_INCIDENT_KEY_PREFIX}{fb_ad_id}"
    assert await notify_recurring_incident(
        pg_engine,
        incident_key=per_ad_key,
        audience="owners",
        event_type="autostop_undelivered_pause",
        severity="critical",
        title="Auto-stop pause undelivered",
    )

    lease_owner = uuid.uuid4()
    payload = json.dumps(
        {
            "mutation_kind": "pause_ad",
            "target_id": fb_ad_id,
            "params": {},
            "ad_account_id": "123",
        }
    )
    async with pg_engine.begin() as conn:
        task_id = (
            await conn.execute(
                text(
                    """
                    INSERT INTO task_queue
                        (task_type, status, idempotency_key, payload, requested_by,
                         lane, lease_owner, lease_token, lease_expires_at)
                    VALUES
                        ('meta_api_mutation', 'running', :key, CAST(:payload AS JSONB),
                         'bot_auto_stop', 'money', :lease_owner, 9,
                         NOW() + INTERVAL '30 seconds')
                    RETURNING id
                    """
                ),
                {
                    "key": f"test:autostop:rollback:{uuid.uuid4()}",
                    "payload": payload,
                    "lease_owner": lease_owner,
                },
            )
        ).scalar_one()

    original = worker_notify.resolve_recurring_incident_in_transaction

    async def _fail_per_ad(conn, *, incident_key, audience, summary):
        if incident_key == per_ad_key:
            raise RuntimeError("simulated crash before per-ad recovery projection")
        return await original(
            conn,
            incident_key=incident_key,
            audience=audience,
            summary=summary,
        )

    monkeypatch.setattr(
        worker_notify,
        "resolve_recurring_incident_in_transaction",
        _fail_per_ad,
    )

    with pytest.raises(RuntimeError, match="simulated crash"):
        await mark_succeeded(
            pg_engine,
            task_id=task_id,
            result={"outcome": "CONFIRMED", "status": "PAUSED"},
            lease_owner=lease_owner,
            lease_token=9,
        )

    async with pg_engine.connect() as conn:
        task_status = await conn.scalar(
            text("SELECT status FROM task_queue WHERE id = :task_id"),
            {"task_id": task_id},
        )
        incident_status = await conn.scalar(
            text("SELECT status FROM incidents WHERE incident_key = :per_ad_key"),
            {"per_ad_key": per_ad_key},
        )
        recovery_count = await conn.scalar(
            text("SELECT COUNT(*) FROM notification_events WHERE event_type = 'incident_recovered'")
        )

    assert task_status == "running"
    assert incident_status == "open"
    assert recovery_count == 0


@pytest.mark.asyncio
async def test_autostop_unknown_terminal_never_resolves_recurring_incidents(
    pg_engine,
    clean_worker_incidents,
) -> None:
    fb_ad_id = "120246662749510088"
    per_ad_key = f"{UNDELIVERED_INCIDENT_KEY_PREFIX}{fb_ad_id}"
    for incident_key, audience in (
        (AUTOSTOP_CHANNEL_INCIDENT_KEY, "all"),
        (per_ad_key, "owners"),
    ):
        assert await notify_recurring_incident(
            pg_engine,
            incident_key=incident_key,
            audience=audience,
            event_type="autostop_unknown_guard",
            severity="critical",
            title="Auto-stop state unknown",
        )

    lease_owner = uuid.uuid4()
    async with pg_engine.begin() as conn:
        task_id = (
            await conn.execute(
                text(
                    """
                    INSERT INTO task_queue
                        (task_type, status, idempotency_key, payload, requested_by,
                         lane, lease_owner, lease_token, lease_expires_at)
                    VALUES
                        ('meta_api_mutation', 'running', :key,
                         jsonb_build_object(
                             'mutation_kind', 'pause_ad',
                             'target_id', CAST(:target_id AS text),
                             'ad_account_id', '123',
                             'params', '{}'::jsonb
                         ),
                         'bot_auto_stop', 'money', :lease_owner, 11,
                         NOW() + INTERVAL '30 seconds')
                    RETURNING id
                    """
                ),
                {
                    "key": f"test:autostop:unknown:{uuid.uuid4()}",
                    "target_id": fb_ad_id,
                    "lease_owner": lease_owner,
                },
            )
        ).scalar_one()

    assert await mark_succeeded(
        pg_engine,
        task_id=task_id,
        result={"outcome": "UNKNOWN", "reconcile_required": True},
        lease_owner=lease_owner,
        lease_token=11,
    )

    async with pg_engine.connect() as conn:
        statuses = (
            await conn.execute(
                text(
                    """
                    SELECT incident_key, status
                    FROM incidents
                    WHERE incident_key IN (:channel_key, :per_ad_key)
                    ORDER BY incident_key
                    """
                ),
                {
                    "channel_key": AUTOSTOP_CHANNEL_INCIDENT_KEY,
                    "per_ad_key": per_ad_key,
                },
            )
        ).all()
        recovery_count = await conn.scalar(
            text("SELECT COUNT(*) FROM notification_events WHERE event_type = 'incident_recovered'")
        )

    assert statuses == [
        (AUTOSTOP_CHANNEL_INCIDENT_KEY, "open"),
        (per_ad_key, "open"),
    ]
    assert recovery_count == 0

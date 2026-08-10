from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from apps.cleanup_worker.worker import delete_terminal_notification_control_plane


@pytest.mark.asyncio
async def test_notification_retention_preserves_active_and_nonterminal_boundaries(
    pg_engine,
) -> None:
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=30)
    recipient_id = uuid.uuid4()
    active_incident = uuid.uuid4()
    terminal_incident = uuid.uuid4()
    active_event = uuid.uuid4()
    terminal_incident_event = uuid.uuid4()
    pending_event = uuid.uuid4()
    terminal_event = uuid.uuid4()
    update_delete = 8_000_000_000_000 + uuid.uuid4().int % 1_000_000_000_000
    update_keep = update_delete + 1
    token_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    suffix = uuid.uuid4().int % 1_000_000_000
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_recipients
                        (id, chat_id, telegram_user_id, role)
                    VALUES (:id, :chat_id, :user_id, 'owner')
                    """
                ),
                {
                    "id": recipient_id,
                    "chat_id": 11_000_000_000 + suffix,
                    "user_id": 12_000_000_000 + suffix,
                },
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO incidents
                        (id, incident_key, resource_type, resource_id, severity,
                         status, title, opened_at, resolved_at, created_at, updated_at)
                    VALUES
                        (:active_id, :active_key, 'ad', 'active-ad', 'critical',
                         'open', 'active', :old, NULL, :old, :old),
                        (:terminal_id, :terminal_key, 'ad', 'terminal-ad', 'warning',
                         'resolved', 'terminal', :old, :old, :old, :old)
                    """
                ),
                {
                    "active_id": active_incident,
                    "active_key": f"retention:active:{suffix}",
                    "terminal_id": terminal_incident,
                    "terminal_key": f"retention:terminal:{suffix}",
                    "old": old,
                },
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO notification_events
                        (id, incident_id, event_type, severity, audience, facts,
                         dedupe_key, correlation_id, created_at)
                    VALUES
                        (:active_event, :active_incident, 'incident_warning',
                         'critical', 'owners', '{"title":"active"}'::jsonb,
                         :active_key, gen_random_uuid(), :old),
                        (:terminal_incident_event, :terminal_incident,
                         'incident_recovered', 'ok', 'owners',
                         '{"title":"terminal"}'::jsonb, :terminal_incident_key,
                         gen_random_uuid(), :old),
                        (:pending_event, NULL, 'system_pending', 'warning', 'owners',
                         '{"title":"pending"}'::jsonb, :pending_key,
                         gen_random_uuid(), :old),
                        (:terminal_event, NULL, 'system_terminal', 'warning', 'owners',
                         '{"title":"terminal"}'::jsonb, :terminal_key,
                         gen_random_uuid(), :old)
                    """
                ),
                {
                    "active_event": active_event,
                    "active_incident": active_incident,
                    "active_key": f"retention:event:active:{suffix}",
                    "terminal_incident_event": terminal_incident_event,
                    "terminal_incident": terminal_incident,
                    "terminal_incident_key": f"retention:event:incident:{suffix}",
                    "pending_event": pending_event,
                    "pending_key": f"retention:event:pending:{suffix}",
                    "terminal_event": terminal_event,
                    "terminal_key": f"retention:event:terminal:{suffix}",
                    "old": old,
                },
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO notification_deliveries
                        (event_id, recipient_id, bot_generation, state,
                         completed_at, created_at, updated_at)
                    VALUES
                        (:active_event, :recipient_id, 1, 'sent', :old, :old, :old),
                        (:terminal_incident_event, :recipient_id, 1, 'sent', :old, :old, :old),
                        (:pending_event, :recipient_id, 1, 'pending', NULL, :old, :old),
                        (:terminal_event, :recipient_id, 1, 'sent', :old, :old, :old)
                    """
                ),
                {
                    "active_event": active_event,
                    "terminal_incident_event": terminal_incident_event,
                    "pending_event": pending_event,
                    "terminal_event": terminal_event,
                    "recipient_id": recipient_id,
                    "old": old,
                },
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_updates_inbox
                        (bot_generation, update_id, payload, state,
                         processed_at, received_at)
                    VALUES
                        (1, :delete_id, '{}'::jsonb, 'processed', :old, :old),
                        (1, :keep_id, '{}'::jsonb, 'processed', :old, :old)
                    """
                ),
                {"delete_id": update_delete, "keep_id": update_keep, "old": old},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_command_replies
                        (bot_generation, update_id, ordinal, chat_id, text,
                         state, created_at, updated_at)
                    VALUES (1, :update_id, 0, 42, 'pending', 'pending', :old, :old)
                    """
                ),
                {"update_id": update_keep, "old": old},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_action_tokens
                        (id, token_digest, recipient_id, action_key, action_kind,
                         target_type, target_id, expires_at, created_at)
                    VALUES
                        (:expired_id, :expired_digest, :recipient_id, 'pause', 'pause_ad',
                         'fb_ad', 'ad-old', :old, :old),
                        (:active_id, :active_digest, :recipient_id, 'pause', 'pause_ad',
                         'fb_ad', 'ad-new', :future, :old)
                    """
                ),
                {
                    "expired_id": token_ids[0],
                    "expired_digest": hashlib.sha256(b"expired-action").digest(),
                    "active_id": token_ids[1],
                    "active_digest": hashlib.sha256(b"active-action").digest(),
                    "recipient_id": recipient_id,
                    "old": old,
                    "future": now + timedelta(days=1),
                },
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_navigation_tokens
                        (id, token_digest, recipient_id, target_kind, target_id,
                         expires_at, created_at)
                    VALUES
                        (:expired_id, :expired_digest, :recipient_id, 'ad', 'ad-old',
                         :old, :old),
                        (:active_id, :active_digest, :recipient_id, 'ad', 'ad-new',
                         :future, :old)
                    """
                ),
                {
                    "expired_id": token_ids[2],
                    "expired_digest": hashlib.sha256(b"expired-navigation").digest(),
                    "active_id": token_ids[3],
                    "active_digest": hashlib.sha256(b"active-navigation").digest(),
                    "recipient_id": recipient_id,
                    "old": old,
                    "future": now + timedelta(days=1),
                },
            )

        counts = await delete_terminal_notification_control_plane(
            pg_engine,
            {
                "incidents_terminal": "1 day",
                "notification_events_terminal": "1 day",
                "telegram_action_tokens_terminal": "1 day",
                "telegram_navigation_tokens_terminal": "1 day",
                "telegram_updates_terminal": "1 day",
                "telegram_command_replies_terminal": "1 day",
            },
            now=now,
        )
        assert counts["incidents"] >= 1
        assert counts["notification_events"] >= 2
        assert counts["notification_deliveries"] >= 2

        async with pg_engine.connect() as conn:
            incidents = set(
                (
                    await conn.execute(
                        text("SELECT id FROM incidents WHERE id = ANY(:ids)"),
                        {"ids": [active_incident, terminal_incident]},
                    )
                ).scalars()
            )
            events = set(
                (
                    await conn.execute(
                        text("SELECT id FROM notification_events WHERE id = ANY(:ids)"),
                        {
                            "ids": [
                                active_event,
                                terminal_incident_event,
                                pending_event,
                                terminal_event,
                            ]
                        },
                    )
                ).scalars()
            )
            updates = set(
                (
                    await conn.execute(
                        text(
                            "SELECT update_id FROM telegram_updates_inbox "
                            "WHERE update_id = ANY(:ids)"
                        ),
                        {"ids": [update_delete, update_keep]},
                    )
                ).scalars()
            )
            tokens = set(
                (
                    await conn.execute(
                        text(
                            "SELECT id FROM telegram_action_tokens WHERE id = ANY(:ids) "
                            "UNION ALL "
                            "SELECT id FROM telegram_navigation_tokens WHERE id = ANY(:ids)"
                        ),
                        {"ids": token_ids},
                    )
                ).scalars()
            )
        assert incidents == {active_incident}
        assert events == {active_event, pending_event}
        assert updates == {update_keep}
        assert tokens == {token_ids[1], token_ids[3]}
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM telegram_updates_inbox WHERE update_id = ANY(:ids)"),
                {"ids": [update_delete, update_keep]},
            )
            await conn.execute(
                text("DELETE FROM telegram_action_tokens WHERE recipient_id = :recipient_id"),
                {"recipient_id": recipient_id},
            )
            await conn.execute(
                text("DELETE FROM telegram_navigation_tokens WHERE recipient_id = :recipient_id"),
                {"recipient_id": recipient_id},
            )
            await conn.execute(
                text("DELETE FROM notification_deliveries WHERE recipient_id = :recipient_id"),
                {"recipient_id": recipient_id},
            )
            await conn.execute(
                text("DELETE FROM notification_events WHERE id = ANY(:ids)"),
                {
                    "ids": [
                        active_event,
                        terminal_incident_event,
                        pending_event,
                        terminal_event,
                    ]
                },
            )
            await conn.execute(
                text("DELETE FROM incidents WHERE id = ANY(:ids)"),
                {"ids": [active_incident, terminal_incident]},
            )
            await conn.execute(
                text("DELETE FROM telegram_recipients WHERE id = :recipient_id"),
                {"recipient_id": recipient_id},
            )

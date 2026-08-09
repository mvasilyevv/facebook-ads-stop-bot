"""Real PostgreSQL contracts for serialized notification and incident lifecycles."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import text

from apps.api.routers.v1.schemas.settings_telegram import TelegramRecipientPreferenceRequest
from apps.api.routers.v1.settings_telegram import put_telegram_recipient_preferences
from core.crypto import encrypt
from core.incidents.service import (
    IncidentGenerationMismatchError,
    acknowledge_incident,
)
from core.tasks.queue import (
    claim_browser_ready_task,
    create_task,
    expire_overdue_tasks,
    mark_succeeded,
)
from core.telegram.action_tokens import (
    claim_action_token,
    complete_action_token,
    mint_action_token,
)
from core.telegram.gateway import (
    TelegramFailureKind,
    TelegramGatewayError,
    telegram_credential_fingerprint,
)
from core.telegram.handlers.alerts import handle_action_callback as _handle_action_callback
from core.telegram.navigation_tokens import mint_navigation_token
from core.telegram.notifications import (
    build_incident_reissue_spec,
    disable_recipient_delivery_in_transaction,
    enqueue_notification_in_transaction,
    mark_delivery_failure,
    mark_delivery_sent,
    mark_delivery_superseded,
    open_telegram_auth_incident_in_transaction,
    reconcile_expired_delivery_leases,
    refresh_telegram_auth_gate,
    retire_revoked_recipient_backlog_in_transaction,
    verify_telegram_authentication,
)
from core.telegram.notifications import (
    claim_notification_delivery as _claim_notification_delivery,
)
from core.telegram.notifications import (
    mark_delivery_external_started as _mark_delivery_external_started,
)
from core.telegram.schemas import (
    NotificationActionSpec,
    NotificationCardFacts,
    NotificationEventSpec,
    NotificationNavigationTarget,
    TelegramWebhookUpdate,
)
from core.telegram.update_inbox import (
    claim_telegram_update,
    mark_telegram_update_failed,
)
from core.telegram.update_inbox import (
    persist_telegram_update as _persist_telegram_update,
)
from core.telegram.webhook_configuration import (
    resolve_webhook_target,
    store_rotated_token_and_schedule_webhook,
)

pytestmark = pytest.mark.usefixtures(
    "fresh_browser_readiness",
    "authoritative_telegram_config",
)

_BOT_TOKEN = "integration-telegram-authority-token"
_BOT_GENERATION = 4242
_BOT_FINGERPRINT = telegram_credential_fingerprint(_BOT_TOKEN)


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


async def handle_action_callback(**kwargs):
    return await _handle_action_callback(bot_generation=_BOT_GENERATION, **kwargs)


async def persist_telegram_update(conn, update):
    return await _persist_telegram_update(
        conn,
        update,
        bot_generation=_BOT_GENERATION,
    )


@dataclass
class _Resources:
    recipient_ids: list[uuid.UUID] = field(default_factory=list)
    incident_ids: list[uuid.UUID] = field(default_factory=list)
    event_ids: list[uuid.UUID] = field(default_factory=list)
    task_ids: list[int] = field(default_factory=list)
    update_ids: list[int] = field(default_factory=list)


@pytest_asyncio.fixture
async def notification_resources(pg_engine):
    resources = _Resources()
    yield resources
    async with pg_engine.begin() as conn:
        if resources.update_ids:
            await conn.execute(
                text(
                    "DELETE FROM telegram_updates_inbox "
                    "WHERE update_id = ANY(CAST(:ids AS bigint[]))"
                ),
                {"ids": resources.update_ids},
            )
        if resources.recipient_ids:
            await conn.execute(
                text(
                    "DELETE FROM telegram_action_tokens "
                    "WHERE recipient_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": resources.recipient_ids},
            )
            await conn.execute(
                text(
                    "DELETE FROM telegram_message_slots "
                    "WHERE recipient_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": resources.recipient_ids},
            )
            await conn.execute(
                text(
                    "DELETE FROM notification_deliveries "
                    "WHERE recipient_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": resources.recipient_ids},
            )
        if resources.event_ids:
            await conn.execute(
                text("DELETE FROM notification_events WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": resources.event_ids},
            )
        if resources.task_ids:
            await conn.execute(
                text("DELETE FROM task_queue WHERE id = ANY(CAST(:ids AS bigint[]))"),
                {"ids": resources.task_ids},
            )
        if resources.incident_ids:
            await conn.execute(
                text("DELETE FROM incidents WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": resources.incident_ids},
            )
        if resources.recipient_ids:
            await conn.execute(
                text(
                    "DELETE FROM telegram_recipient_preferences "
                    "WHERE recipient_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": resources.recipient_ids},
            )
            await conn.execute(
                text("DELETE FROM telegram_recipients WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": resources.recipient_ids},
            )


async def _seed_recipient_and_incident(pg_engine, resources: _Resources):
    recipient_id = uuid.uuid4()
    incident_id = uuid.uuid4()
    correlation_id = uuid.uuid4()
    suffix = uuid.uuid4().int % 1_000_000_000
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
                "chat_id": 8_000_000_000 + suffix,
                "user_id": 9_000_000_000 + suffix,
            },
        )
        await conn.execute(
            text(
                """
                INSERT INTO incidents
                    (id, incident_key, generation, resource_type, resource_id,
                     severity, status, title, correlation_id)
                VALUES
                    (:id, :key, 1, 'ad', :resource_id,
                     'critical', 'open', 'Test incident', :correlation_id)
                """
            ),
            {
                "id": incident_id,
                "key": f"test:{incident_id}",
                "resource_id": f"ad-{suffix}",
                "correlation_id": correlation_id,
            },
        )
    resources.recipient_ids.append(recipient_id)
    resources.incident_ids.append(incident_id)
    return recipient_id, incident_id, correlation_id


async def _wait_for_blocked_backend(
    pg_engine,
    *,
    query_fragment: str,
    wait_event: str | None = None,
) -> None:
    deadline = asyncio.get_running_loop().time() + 3.0
    while asyncio.get_running_loop().time() < deadline:
        async with pg_engine.connect() as conn:
            blocked = await conn.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_stat_activity
                        WHERE datname = current_database()
                          AND pid <> pg_backend_pid()
                          AND wait_event_type = 'Lock'
                          AND (
                              CAST(:wait_event AS text) IS NULL
                              OR wait_event = CAST(:wait_event AS text)
                          )
                          AND POSITION(:query_fragment IN query) > 0
                    )
                    """
                ),
                {
                    "query_fragment": query_fragment,
                    "wait_event": wait_event,
                },
            )
        if blocked:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"backend did not block on {query_fragment!r} wait_event={wait_event!r}")


async def _manual_notification_opt_out(pg_engine, recipient_id: uuid.UUID) -> None:
    await put_telegram_recipient_preferences(
        recipient_id=recipient_id,
        body=TelegramRecipientPreferenceRequest(is_enabled=False),
        engine=pg_engine,
    )


async def _enqueue_incident_warning(
    pg_engine,
    resources: _Resources,
    *,
    incident_id: uuid.UUID,
    correlation_id: uuid.UUID,
    expected_delivery_count: int = 1,
    audience: Literal["owners", "all"] = "owners",
):
    async with pg_engine.begin() as conn:
        result = await enqueue_notification_in_transaction(
            conn,
            NotificationEventSpec(
                event_type="incident_warning",
                severity="critical",
                audience=audience,
                facts=NotificationCardFacts(title="Threshold breached", status="OPEN"),
                dedupe_key=f"test:warning:{incident_id}:{uuid.uuid4()}",
                incident_id=incident_id,
                correlation_id=correlation_id,
            ),
        )
    resources.event_ids.append(result.event_id)
    assert result.delivery_count == expected_delivery_count
    return result


def _rotation_target():
    return resolve_webhook_target(
        frontend_origin="https://operator.example.test",
        secret_token=SecretStr("notification-rotation-secret"),
    )


async def _rotate_notification_bot(conn) -> None:
    token = f"rotated-notification-token-{uuid.uuid4()}"
    await store_rotated_token_and_schedule_webhook(
        conn,
        bot_token_encrypted=encrypt(token),
        bot_token_fingerprint=telegram_credential_fingerprint(token),
        target=_rotation_target(),
    )


@pytest.mark.asyncio
async def test_enqueue_then_rotation_rebinds_one_preboundary_delivery(
    pg_engine,
    notification_resources,
) -> None:
    recipient_id, incident_id, correlation_id = await _seed_recipient_and_incident(
        pg_engine, notification_resources
    )
    conn = await pg_engine.connect()
    tx = await conn.begin()
    rotation_task = None
    try:
        result = await enqueue_notification_in_transaction(
            conn,
            NotificationEventSpec(
                event_type="incident_warning",
                severity="critical",
                audience="owners",
                facts=NotificationCardFacts(title="Rotation race", status="OPEN"),
                dedupe_key=f"test:enqueue-before-rotation:{uuid.uuid4()}",
                incident_id=incident_id,
                correlation_id=correlation_id,
            ),
        )
        notification_resources.event_ids.append(result.event_id)

        async def rotate() -> None:
            async with pg_engine.begin() as rotation_conn:
                await _rotate_notification_bot(rotation_conn)

        rotation_task = asyncio.create_task(rotate())
        await _wait_for_blocked_backend(
            pg_engine,
            query_fragment="SELECT webhook_generation",
        )
        await tx.commit()
        await asyncio.wait_for(rotation_task, timeout=3.0)
    finally:
        if tx.is_active:
            await tx.rollback()
        if rotation_task is not None and not rotation_task.done():
            rotation_task.cancel()
            await asyncio.gather(rotation_task, return_exceptions=True)
        await conn.close()

    async with pg_engine.connect() as check:
        config_generation = await check.scalar(
            text("SELECT webhook_generation FROM telegram_config WHERE singleton_key='default'")
        )
        rows = (
            await check.execute(
                text(
                    """
                    SELECT bot_generation, state
                    FROM notification_deliveries
                    WHERE event_id = :event_id AND recipient_id = :recipient_id
                    """
                ),
                {"event_id": result.event_id, "recipient_id": recipient_id},
            )
        ).all()
    assert [(row.bot_generation, row.state) for row in rows] == [(config_generation, "pending")]


@pytest.mark.asyncio
async def test_rotation_then_enqueue_uses_new_generation_once(
    pg_engine,
    notification_resources,
) -> None:
    recipient_id, incident_id, correlation_id = await _seed_recipient_and_incident(
        pg_engine, notification_resources
    )
    rotation_conn = await pg_engine.connect()
    rotation_tx = await rotation_conn.begin()
    enqueue_task = None
    try:
        await _rotate_notification_bot(rotation_conn)

        async def enqueue():
            async with pg_engine.begin() as conn:
                return await enqueue_notification_in_transaction(
                    conn,
                    NotificationEventSpec(
                        event_type="incident_warning",
                        severity="critical",
                        audience="owners",
                        facts=NotificationCardFacts(title="Rotation first", status="OPEN"),
                        dedupe_key=f"test:rotation-before-enqueue:{uuid.uuid4()}",
                        incident_id=incident_id,
                        correlation_id=correlation_id,
                    ),
                )

        enqueue_task = asyncio.create_task(enqueue())
        await _wait_for_blocked_backend(
            pg_engine,
            query_fragment="SELECT webhook_generation",
        )
        await rotation_tx.commit()
        result = await asyncio.wait_for(enqueue_task, timeout=3.0)
        notification_resources.event_ids.append(result.event_id)
    finally:
        if rotation_tx.is_active:
            await rotation_tx.rollback()
        if enqueue_task is not None and not enqueue_task.done():
            enqueue_task.cancel()
            await asyncio.gather(enqueue_task, return_exceptions=True)
        await rotation_conn.close()

    async with pg_engine.connect() as check:
        config_generation = await check.scalar(
            text("SELECT webhook_generation FROM telegram_config WHERE singleton_key='default'")
        )
        rows = (
            await check.execute(
                text(
                    """
                    SELECT bot_generation, state
                    FROM notification_deliveries
                    WHERE event_id = :event_id AND recipient_id = :recipient_id
                    """
                ),
                {"event_id": result.event_id, "recipient_id": recipient_id},
            )
        ).all()
    assert [(row.bot_generation, row.state) for row in rows] == [(config_generation, "pending")]


@pytest.mark.asyncio
async def test_notification_disable_before_claim_retires_stale_generation(
    pg_engine,
    notification_resources,
) -> None:
    recipient_id, incident_id, correlation_id = await _seed_recipient_and_incident(
        pg_engine,
        notification_resources,
    )
    warning = await _enqueue_incident_warning(
        pg_engine,
        notification_resources,
        incident_id=incident_id,
        correlation_id=correlation_id,
    )
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE telegram_config SET is_enabled = FALSE, updated_at = NOW() "
                "WHERE singleton_key = 'default'"
            )
        )

    assert await claim_notification_delivery(pg_engine, worker_id="disabled-before-claim") is None
    async with pg_engine.connect() as conn:
        state, error_code = (
            await conn.execute(
                text(
                    "SELECT state, last_error_code FROM notification_deliveries "
                    "WHERE event_id = :event_id AND recipient_id = :recipient_id"
                ),
                {"event_id": warning.event_id, "recipient_id": recipient_id},
            )
        ).one()
    assert (state, error_code) == ("superseded", "stale_bot_generation")


@pytest.mark.asyncio
async def test_notification_rotation_after_claim_fences_external_boundary(
    pg_engine,
    notification_resources,
) -> None:
    recipient_id, incident_id, correlation_id = await _seed_recipient_and_incident(
        pg_engine,
        notification_resources,
    )
    await _enqueue_incident_warning(
        pg_engine,
        notification_resources,
        incident_id=incident_id,
        correlation_id=correlation_id,
    )
    claim = await claim_notification_delivery(pg_engine, worker_id="rotate-after-claim")
    assert claim is not None
    rotated_fingerprint = bytes.fromhex(telegram_credential_fingerprint("rotated-token"))
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE telegram_config
                SET bot_token_encrypted = 'rotated-test-token',
                    bot_token_fingerprint = :fingerprint,
                    webhook_generation = :generation,
                    webhook_applied_generation = :generation,
                    webhook_operation = 'configure',
                    webhook_state = 'configured',
                    updated_at = NOW()
                WHERE singleton_key = 'default'
                """
            ),
            {"fingerprint": rotated_fingerprint, "generation": _BOT_GENERATION + 1},
        )

    assert (
        await mark_delivery_external_started(
            pg_engine,
            claim=claim,
            operation_kind="send",
        )
        == "superseded"
    )
    async with pg_engine.connect() as conn:
        state, external_started_at, error_code = (
            await conn.execute(
                text(
                    "SELECT state, external_started_at, last_error_code "
                    "FROM notification_deliveries WHERE id = :delivery_id"
                ),
                {"delivery_id": claim.delivery_id},
            )
        ).one()
    assert state == "superseded"
    assert external_started_at is None
    assert error_code == "stale_bot_generation"


@pytest.mark.asyncio
async def test_stale_delivery_401_after_rotation_does_not_open_global_auth_incident(
    pg_engine,
    notification_resources,
) -> None:
    _, incident_id, correlation_id = await _seed_recipient_and_incident(
        pg_engine, notification_resources
    )
    await _enqueue_incident_warning(
        pg_engine,
        notification_resources,
        incident_id=incident_id,
        correlation_id=correlation_id,
    )
    claim = await claim_notification_delivery(pg_engine, worker_id="stale-delivery-401")
    assert claim is not None
    assert (
        await mark_delivery_external_started(
            pg_engine,
            claim=claim,
            operation_kind="send",
        )
        == "ready"
    )
    async with pg_engine.begin() as conn:
        await _rotate_notification_bot(conn)

    decision = await mark_delivery_failure(
        pg_engine,
        claim=claim,
        error=TelegramGatewayError(
            method="sendMessage",
            kind=TelegramFailureKind.UNAUTHORIZED,
            error_code=401,
            description="Unauthorized",
        ),
        credential_fingerprint=_BOT_FINGERPRINT,
    )

    assert (decision.state, decision.error_code, decision.finalized) == (
        "dead",
        "stale_bot_generation",
        True,
    )
    async with pg_engine.connect() as conn:
        persisted = (
            await conn.execute(
                text("SELECT state, last_error_code FROM notification_deliveries WHERE id=:id"),
                {"id": claim.delivery_id},
            )
        ).one()
        auth_incidents = await conn.scalar(
            text("SELECT COUNT(*) FROM incidents WHERE incident_key='telegram:bot-auth'")
        )
    assert (persisted.state, persisted.last_error_code) == (
        "dead",
        "stale_bot_generation",
    )
    assert auth_incidents == 0


@pytest.mark.asyncio
async def test_current_delivery_401_opens_auth_incident_and_retries(
    pg_engine,
    notification_resources,
) -> None:
    _, incident_id, correlation_id = await _seed_recipient_and_incident(
        pg_engine, notification_resources
    )
    await _enqueue_incident_warning(
        pg_engine,
        notification_resources,
        incident_id=incident_id,
        correlation_id=correlation_id,
    )
    claim = await claim_notification_delivery(pg_engine, worker_id="current-delivery-401")
    assert claim is not None
    assert (
        await mark_delivery_external_started(
            pg_engine,
            claim=claim,
            operation_kind="send",
        )
        == "ready"
    )
    decision = await mark_delivery_failure(
        pg_engine,
        claim=claim,
        error=TelegramGatewayError(
            method="sendMessage",
            kind=TelegramFailureKind.UNAUTHORIZED,
            error_code=401,
            description="Unauthorized",
        ),
        credential_fingerprint=_BOT_FINGERPRINT,
    )
    assert (decision.state, decision.error_code, decision.finalized) == (
        "retry",
        "telegram_unauthorized",
        True,
    )
    async with pg_engine.connect() as conn:
        auth_incidents = await conn.scalar(
            text(
                """
                SELECT COUNT(*) FROM incidents
                WHERE incident_key='telegram:bot-auth' AND status='open'
                """
            )
        )
    assert auth_incidents == 1
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                DELETE FROM notification_deliveries d USING notification_events e,
                    incidents i
                WHERE d.event_id=e.id AND e.incident_id=i.id
                  AND i.incident_key='telegram:bot-auth'
                """
            )
        )
        await conn.execute(
            text(
                """
                DELETE FROM notification_events e USING incidents i
                WHERE e.incident_id=i.id AND i.incident_key='telegram:bot-auth'
                """
            )
        )
        await conn.execute(text("DELETE FROM incidents WHERE incident_key='telegram:bot-auth'"))


@pytest.mark.asyncio
async def test_stale_update_401_after_rotation_does_not_open_global_auth_incident(
    pg_engine,
    notification_resources,
) -> None:
    update_id = 8_800_000_000_000 + uuid.uuid4().int % 1_000_000_000
    update = TelegramWebhookUpdate.model_validate({"update_id": update_id})
    async with pg_engine.begin() as conn:
        assert await persist_telegram_update(conn, update)
    notification_resources.update_ids.append(update_id)
    claim = await claim_telegram_update(pg_engine, worker_id="stale-update-401")
    assert claim is not None and claim.update_id == update_id
    async with pg_engine.begin() as conn:
        await _rotate_notification_bot(conn)

    assert await mark_telegram_update_failed(
        pg_engine,
        claim=claim,
        error_code="telegram_unauthorized",
        gateway_error=TelegramGatewayError(
            method="answerCallbackQuery",
            kind=TelegramFailureKind.UNAUTHORIZED,
            error_code=401,
            description="Unauthorized",
        ),
        credential_fingerprint=_BOT_FINGERPRINT,
    )
    async with pg_engine.connect() as conn:
        persisted = (
            await conn.execute(
                text(
                    """
                    SELECT state, last_error_code
                    FROM telegram_updates_inbox
                    WHERE bot_generation=:generation AND update_id=:update_id
                    """
                ),
                {"generation": _BOT_GENERATION, "update_id": update_id},
            )
        ).one()
        auth_incidents = await conn.scalar(
            text("SELECT COUNT(*) FROM incidents WHERE incident_key='telegram:bot-auth'")
        )
    assert (persisted.state, persisted.last_error_code) == (
        "dead",
        "stale_bot_generation",
    )
    assert auth_incidents == 0


@pytest.mark.asyncio
async def test_current_update_401_opens_auth_incident_and_retries(
    pg_engine,
    notification_resources,
) -> None:
    update_id = 8_810_000_000_000 + uuid.uuid4().int % 1_000_000_000
    update = TelegramWebhookUpdate.model_validate({"update_id": update_id})
    async with pg_engine.begin() as conn:
        assert await persist_telegram_update(conn, update)
    notification_resources.update_ids.append(update_id)
    claim = await claim_telegram_update(pg_engine, worker_id="current-update-401")
    assert claim is not None
    assert await mark_telegram_update_failed(
        pg_engine,
        claim=claim,
        error_code="telegram_unauthorized",
        gateway_error=TelegramGatewayError(
            method="answerCallbackQuery",
            kind=TelegramFailureKind.UNAUTHORIZED,
            error_code=401,
            description="Unauthorized",
        ),
        credential_fingerprint=_BOT_FINGERPRINT,
    )
    async with pg_engine.connect() as conn:
        state = await conn.scalar(
            text(
                """
                SELECT state FROM telegram_updates_inbox
                WHERE bot_generation=:generation AND update_id=:update_id
                """
            ),
            {"generation": _BOT_GENERATION, "update_id": update_id},
        )
        auth_incidents = await conn.scalar(
            text("SELECT COUNT(*) FROM incidents WHERE incident_key='telegram:bot-auth'")
        )
    assert state == "retry"
    assert auth_incidents == 1
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                DELETE FROM notification_deliveries d USING notification_events e,
                    incidents i
                WHERE d.event_id=e.id AND e.incident_id=i.id
                  AND i.incident_key='telegram:bot-auth'
                """
            )
        )
        await conn.execute(
            text(
                """
                DELETE FROM notification_events e USING incidents i
                WHERE e.incident_id=i.id AND i.incident_key='telegram:bot-auth'
                """
            )
        )
        await conn.execute(text("DELETE FROM incidents WHERE incident_key='telegram:bot-auth'"))


@pytest.mark.asyncio
async def test_notification_cached_gateway_mismatch_cannot_claim_current_delivery(
    pg_engine,
    notification_resources,
) -> None:
    recipient_id, incident_id, correlation_id = await _seed_recipient_and_incident(
        pg_engine,
        notification_resources,
    )
    warning = await _enqueue_incident_warning(
        pg_engine,
        notification_resources,
        incident_id=incident_id,
        correlation_id=correlation_id,
    )

    assert (
        await _claim_notification_delivery(
            pg_engine,
            worker_id="wrong-generation",
            gateway_generation=_BOT_GENERATION + 1,
            credential_fingerprint=_BOT_FINGERPRINT,
        )
        is None
    )
    assert (
        await _claim_notification_delivery(
            pg_engine,
            worker_id="wrong-fingerprint",
            gateway_generation=_BOT_GENERATION,
            credential_fingerprint="f" * 64,
        )
        is None
    )
    async with pg_engine.connect() as conn:
        state = await conn.scalar(
            text(
                "SELECT state FROM notification_deliveries "
                "WHERE event_id = :event_id AND recipient_id = :recipient_id"
            ),
            {"event_id": warning.event_id, "recipient_id": recipient_id},
        )
    assert state == "pending"


async def _resolve_with_notification(
    pg_engine,
    resources: _Resources,
    *,
    incident_id: uuid.UUID,
    correlation_id: uuid.UUID,
):
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE incidents SET status = 'resolved', resolved_at = NOW(), "
                "updated_at = NOW() WHERE id = :id"
            ),
            {"id": incident_id},
        )
        result = await enqueue_notification_in_transaction(
            conn,
            NotificationEventSpec(
                event_type="incident_recovered",
                severity="ok",
                audience="owners",
                facts=NotificationCardFacts(title="Threshold breached", status="RESOLVED"),
                dedupe_key=f"test:recovery:{incident_id}:{uuid.uuid4()}",
                incident_id=incident_id,
                correlation_id=correlation_id,
            ),
        )
    resources.event_ids.append(result.event_id)
    return result


@pytest.mark.asyncio
async def test_pre_boundary_incident_transition_fences_stale_open_card(
    pg_engine,
    notification_resources,
) -> None:
    _, incident_id, correlation_id = await _seed_recipient_and_incident(
        pg_engine, notification_resources
    )
    warning = await _enqueue_incident_warning(
        pg_engine,
        notification_resources,
        incident_id=incident_id,
        correlation_id=correlation_id,
    )
    claim = await claim_notification_delivery(pg_engine, worker_id="pre-boundary")
    assert claim is not None and claim.event_id == warning.event_id

    recovery = await _resolve_with_notification(
        pg_engine,
        notification_resources,
        incident_id=incident_id,
        correlation_id=correlation_id,
    )
    # No card existed and the predecessor had not crossed the I/O boundary, so
    # a recovery message would be noise. The stale OPEN send is fenced instead.
    assert recovery.delivery_count == 0
    assert (
        await mark_delivery_external_started(
            pg_engine,
            claim=claim,
            operation_kind="send",
        )
        == "superseded"
    )

    async with pg_engine.connect() as conn:
        state = await conn.scalar(
            text("SELECT state FROM notification_deliveries WHERE id = :id"),
            {"id": claim.delivery_id},
        )
        slot_count = await conn.scalar(
            text("SELECT COUNT(*) FROM telegram_message_slots WHERE incident_id = :id"),
            {"id": incident_id},
        )
    assert state == "superseded"
    assert slot_count == 0


@pytest.mark.asyncio
async def test_post_boundary_retry_failure_yields_to_terminal_lifecycle(
    pg_engine,
    notification_resources,
) -> None:
    _, incident_id, correlation_id = await _seed_recipient_and_incident(
        pg_engine, notification_resources
    )
    warning = await _enqueue_incident_warning(
        pg_engine,
        notification_resources,
        incident_id=incident_id,
        correlation_id=correlation_id,
    )
    claim = await claim_notification_delivery(pg_engine, worker_id="post-boundary")
    assert claim is not None and claim.event_id == warning.event_id
    assert (
        await mark_delivery_external_started(
            pg_engine,
            claim=claim,
            operation_kind="send",
        )
        == "ready"
    )

    recovery = await _resolve_with_notification(
        pg_engine,
        notification_resources,
        incident_id=incident_id,
        correlation_id=correlation_id,
    )
    # The old send is in flight, so a durable terminal snapshot must exist even
    # though its message slot has not been committed yet.
    assert recovery.delivery_count == 1

    decision = await mark_delivery_failure(
        pg_engine,
        claim=claim,
        error=TelegramGatewayError(
            method="sendMessage",
            kind=TelegramFailureKind.RATE_LIMITED,
            error_code=429,
            retry_after=120,
        ),
    )
    assert decision.state == "superseded"
    assert decision.finalized is True

    terminal_claim = await claim_notification_delivery(
        pg_engine,
        worker_id="terminal-snapshot",
    )
    assert terminal_claim is not None
    assert terminal_claim.event_id == recovery.event_id
    assert terminal_claim.slot_message_id is None
    assert (
        await mark_delivery_external_started(
            pg_engine,
            claim=terminal_claim,
            operation_kind="send",
        )
        == "ready"
    )
    assert await mark_delivery_sent(
        pg_engine,
        claim=terminal_claim,
        message_id=4242,
        render_hash=b"r" * 32,
    )

    async with pg_engine.connect() as conn:
        states = (
            await conn.execute(
                text(
                    "SELECT event_id, state FROM notification_deliveries "
                    "WHERE event_id IN (:warning_id, :recovery_id)"
                ),
                {
                    "warning_id": warning.event_id,
                    "recovery_id": recovery.event_id,
                },
            )
        ).all()
        slot_state = await conn.scalar(
            text("SELECT state FROM telegram_message_slots WHERE incident_id = :id"),
            {"id": incident_id},
        )
    assert {str(row.event_id): row.state for row in states} == {
        str(warning.event_id): "superseded",
        str(recovery.event_id): "sent",
    }
    assert slot_state == "resolved"


@pytest.mark.asyncio
async def test_rate_limit_at_max_attempts_persists_full_retry_after(
    pg_engine,
    notification_resources,
) -> None:
    _, incident_id, correlation_id = await _seed_recipient_and_incident(
        pg_engine, notification_resources
    )
    warning = await _enqueue_incident_warning(
        pg_engine,
        notification_resources,
        incident_id=incident_id,
        correlation_id=correlation_id,
    )
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE notification_deliveries
                SET attempt_count = max_attempts - 1
                WHERE event_id = :event_id
                """
            ),
            {"event_id": warning.event_id},
        )

    claim = await claim_notification_delivery(pg_engine, worker_id="rate-limit-boundary")
    assert claim is not None
    assert claim.attempt_count == claim.max_attempts
    assert (
        await mark_delivery_external_started(
            pg_engine,
            claim=claim,
            operation_kind="send",
        )
        == "ready"
    )

    leaked_action = "a:AbCdEfGhIjKlMnOpQrStUv"
    leaked_navigation = "nav=VwXyZaBcDeFgHiJkLmNoPq"
    decision = await mark_delivery_failure(
        pg_engine,
        claim=claim,
        error=TelegramGatewayError(
            method="sendMessage",
            kind=TelegramFailureKind.RATE_LIMITED,
            error_code=429,
            retry_after=137,
            description=f"rate limit {leaked_action} {leaked_navigation}",
        ),
    )

    assert decision.state == "retry"
    assert decision.finalized is True
    async with pg_engine.connect() as conn:
        persisted = (
            await conn.execute(
                text(
                    """
                    SELECT state, scheduled_at, last_error_code, last_error_detail
                    FROM notification_deliveries
                    WHERE id = :delivery_id
                    """
                ),
                {"delivery_id": claim.delivery_id},
            )
        ).one()
    assert persisted.state == "retry"
    assert persisted.scheduled_at == decision.scheduled_at
    assert persisted.last_error_code == "telegram_rate_limited"
    assert "AbCdEfGhIjKlMnOpQrStUv" not in persisted.last_error_detail
    assert "VwXyZaBcDeFgHiJkLmNoPq" not in persisted.last_error_detail


@pytest.mark.asyncio
async def test_forbidden_delivery_disables_dm_but_preserves_owner_access(
    pg_engine,
    notification_resources,
) -> None:
    recipient_id, incident_id, correlation_id = await _seed_recipient_and_incident(
        pg_engine, notification_resources
    )
    warning = await _enqueue_incident_warning(
        pg_engine,
        notification_resources,
        incident_id=incident_id,
        correlation_id=correlation_id,
    )
    claim = await claim_notification_delivery(pg_engine, worker_id="forbidden-delivery")
    assert claim is not None and claim.event_id == warning.event_id
    assert (
        await mark_delivery_external_started(
            pg_engine,
            claim=claim,
            operation_kind="send",
        )
        == "ready"
    )

    decision = await mark_delivery_failure(
        pg_engine,
        claim=claim,
        error=TelegramGatewayError(
            method="sendMessage",
            kind=TelegramFailureKind.FORBIDDEN,
            error_code=403,
        ),
    )

    assert decision.state == "dead"
    assert decision.disable_recipient_delivery is True
    assert decision.finalized is True
    async with pg_engine.connect() as conn:
        state = (
            await conn.execute(
                text(
                    """
                    SELECT r.role, r.revoked_at, p.is_enabled, d.state,
                           d.last_error_code
                    FROM telegram_recipients r
                    JOIN telegram_recipient_preferences p ON p.recipient_id = r.id
                    JOIN notification_deliveries d ON d.recipient_id = r.id
                    WHERE r.id = :recipient_id AND d.id = :delivery_id
                    """
                ),
                {
                    "recipient_id": recipient_id,
                    "delivery_id": claim.delivery_id,
                },
            )
        ).one()
    assert state.role == "owner"
    assert state.revoked_at is None
    assert state.is_enabled is False
    assert state.state == "dead"
    assert state.last_error_code == "telegram_forbidden"


@pytest.mark.asyncio
async def test_enqueue_and_delivery_disable_serialize_per_recipient(
    pg_engine,
    notification_resources,
) -> None:
    recipient_id, _incident_id, _correlation_id = await _seed_recipient_and_incident(
        pg_engine,
        notification_resources,
    )
    event_id = uuid.uuid4()
    dedupe_key = f"test:enqueue-disable-race:{event_id}"
    async with pg_engine.begin() as conn:
        recipient = (
            await conn.execute(
                text("SELECT chat_id FROM telegram_recipients WHERE id = :id"),
                {"id": recipient_id},
            )
        ).one()
        await conn.execute(
            text(
                """
                INSERT INTO notification_events
                    (id, event_type, severity, audience, facts, dedupe_key)
                VALUES
                    (:id, 'system_delivery_acceptance', 'warning', 'explicit',
                     '{"title":"enqueue-disable race"}'::jsonb, :dedupe_key)
                """
            ),
            {"id": event_id, "dedupe_key": dedupe_key},
        )
    notification_resources.event_ids.append(event_id)

    blocker = await pg_engine.connect()
    blocker_tx = await blocker.begin()
    enqueue_task = None
    disable_task = None
    try:
        # The uncommitted unique competitor blocks only the delivery INSERT.
        # Enqueue has already acquired the recipient lock and read preferences.
        await blocker.execute(
            text(
                """
                INSERT INTO notification_deliveries
                    (event_id, recipient_id, bot_generation,
                     telegram_chat_id, state)
                VALUES
                    (:event_id, :recipient_id, :bot_generation,
                     :chat_id, 'pending')
                """
            ),
            {
                "event_id": event_id,
                "recipient_id": recipient_id,
                "bot_generation": _BOT_GENERATION,
                "chat_id": int(recipient.chat_id),
            },
        )

        async def enqueue() -> object:
            async with pg_engine.begin() as conn:
                return await enqueue_notification_in_transaction(
                    conn,
                    NotificationEventSpec(
                        event_type="system_delivery_acceptance",
                        severity="warning",
                        audience="explicit",
                        explicit_recipient_ids=[recipient_id],
                        facts=NotificationCardFacts(title="enqueue-disable race"),
                        dedupe_key=dedupe_key,
                    ),
                )

        async def disable() -> None:
            async with pg_engine.begin() as conn:
                await disable_recipient_delivery_in_transaction(
                    conn,
                    recipient_id=recipient_id,
                    chat_id=int(recipient.chat_id),
                )

        enqueue_task = asyncio.create_task(enqueue())
        await _wait_for_blocked_backend(
            pg_engine,
            query_fragment="INSERT INTO notification_deliveries",
        )
        disable_task = asyncio.create_task(disable())
        await _wait_for_blocked_backend(
            pg_engine,
            query_fragment="pg_advisory_xact_lock",
            wait_event="advisory",
        )

        await blocker_tx.rollback()
        result, _ = await asyncio.gather(enqueue_task, disable_task)
    finally:
        if blocker_tx.is_active:
            await blocker_tx.rollback()
        for task in (enqueue_task, disable_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (enqueue_task, disable_task) if task is not None),
            return_exceptions=True,
        )
        await blocker.close()

    assert result.delivery_count == 1
    async with pg_engine.connect() as conn:
        persisted = (
            await conn.execute(
                text(
                    """
                    SELECT p.is_enabled, r.revoked_at,
                           COUNT(*) FILTER (
                               WHERE d.state IN ('pending','retry','leased')
                           ) AS deliverable_count,
                           ARRAY_AGG(d.state ORDER BY d.id) AS states
                    FROM telegram_recipients r
                    JOIN telegram_recipient_preferences p ON p.recipient_id = r.id
                    LEFT JOIN notification_deliveries d
                      ON d.recipient_id = r.id AND d.event_id = :event_id
                    WHERE r.id = :recipient_id
                    GROUP BY p.is_enabled, r.revoked_at
                    """
                ),
                {"recipient_id": recipient_id, "event_id": event_id},
            )
        ).one()
    assert persisted.is_enabled is False
    assert persisted.revoked_at is None
    assert int(persisted.deliverable_count) == 0
    assert list(persisted.states) == ["superseded"]


@pytest.mark.asyncio
async def test_manual_opt_out_wins_before_leased_delivery_boundary(
    pg_engine,
    notification_resources,
) -> None:
    recipient_id, incident_id, correlation_id = await _seed_recipient_and_incident(
        pg_engine,
        notification_resources,
    )
    warning = await _enqueue_incident_warning(
        pg_engine,
        notification_resources,
        incident_id=incident_id,
        correlation_id=correlation_id,
    )
    claim = await claim_notification_delivery(pg_engine, worker_id="manual-opt-out-race")
    assert claim is not None and claim.event_id == warning.event_id

    blocker = await pg_engine.connect()
    blocker_tx = await blocker.begin()
    manual_off_task = None
    boundary_task = None
    try:
        await blocker.execute(
            text("SELECT id FROM notification_deliveries WHERE id = :id FOR UPDATE"),
            {"id": claim.delivery_id},
        )
        manual_off_task = asyncio.create_task(_manual_notification_opt_out(pg_engine, recipient_id))
        await _wait_for_blocked_backend(
            pg_engine,
            query_fragment="UPDATE notification_deliveries",
        )

        boundary_task = asyncio.create_task(
            mark_delivery_external_started(
                pg_engine,
                claim=claim,
                operation_kind="send",
            )
        )
        await _wait_for_blocked_backend(
            pg_engine,
            query_fragment="pg_advisory_xact_lock",
            wait_event="advisory",
        )

        await blocker_tx.rollback()
        _manual_result, boundary = await asyncio.gather(
            manual_off_task,
            boundary_task,
        )
    finally:
        if blocker_tx.is_active:
            await blocker_tx.rollback()
        for task in (manual_off_task, boundary_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (manual_off_task, boundary_task) if task is not None),
            return_exceptions=True,
        )
        await blocker.close()

    assert boundary == "lost"
    async with pg_engine.connect() as conn:
        persisted = (
            await conn.execute(
                text(
                    """
                    SELECT d.state, d.external_started_at, d.lease_token,
                           d.last_error_code, p.is_enabled, r.revoked_at
                    FROM notification_deliveries d
                    JOIN telegram_recipients r ON r.id = d.recipient_id
                    JOIN telegram_recipient_preferences p ON p.recipient_id = r.id
                    WHERE d.id = :delivery_id
                    """
                ),
                {"delivery_id": claim.delivery_id},
            )
        ).one()
    assert persisted.state == "superseded"
    assert persisted.external_started_at is None
    assert persisted.lease_token is None
    assert persisted.last_error_code == "recipient_notifications_disabled"
    assert persisted.is_enabled is False
    assert persisted.revoked_at is None


@pytest.mark.asyncio
async def test_manual_opt_out_preserves_post_boundary_delivery_ambiguity(
    pg_engine,
    notification_resources,
) -> None:
    recipient_id, incident_id, correlation_id = await _seed_recipient_and_incident(
        pg_engine,
        notification_resources,
    )
    warning = await _enqueue_incident_warning(
        pg_engine,
        notification_resources,
        incident_id=incident_id,
        correlation_id=correlation_id,
    )
    claim = await claim_notification_delivery(pg_engine, worker_id="post-boundary-opt-out")
    assert claim is not None and claim.event_id == warning.event_id
    assert (
        await mark_delivery_external_started(
            pg_engine,
            claim=claim,
            operation_kind="send",
        )
        == "ready"
    )

    await _manual_notification_opt_out(pg_engine, recipient_id)

    async with pg_engine.connect() as conn:
        persisted = (
            await conn.execute(
                text(
                    """
                    SELECT d.state, d.external_started_at, d.lease_token,
                           d.last_error_code, p.is_enabled, r.revoked_at
                    FROM notification_deliveries d
                    JOIN telegram_recipients r ON r.id = d.recipient_id
                    JOIN telegram_recipient_preferences p ON p.recipient_id = r.id
                    WHERE d.id = :delivery_id
                    """
                ),
                {"delivery_id": claim.delivery_id},
            )
        ).one()
    assert persisted.state == "leased"
    assert persisted.external_started_at is not None
    assert persisted.lease_token == claim.lease_token
    assert persisted.last_error_code is None
    assert persisted.is_enabled is False
    assert persisted.revoked_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("retirement", ["delivery_disabled", "acl_revoked"])
async def test_claimed_action_token_survives_bulk_retirement_until_completion(
    pg_engine,
    notification_resources,
    retirement: str,
) -> None:
    recipient_id, incident_id, correlation_id = await _seed_recipient_and_incident(
        pg_engine,
        notification_resources,
    )
    warning = await _enqueue_incident_warning(
        pg_engine,
        notification_resources,
        incident_id=incident_id,
        correlation_id=correlation_id,
    )
    async with pg_engine.begin() as conn:
        recipient = (
            await conn.execute(
                text("SELECT chat_id, telegram_user_id FROM telegram_recipients WHERE id = :id"),
                {"id": recipient_id},
            )
        ).one()
        delivery_id = await conn.scalar(
            text("SELECT id FROM notification_deliveries WHERE event_id = :event_id"),
            {"event_id": warning.event_id},
        )
        claimed_token = await mint_action_token(
            conn,
            recipient_id=recipient_id,
            delivery_id=int(delivery_id),
            event_id=warning.event_id,
            incident_id=incident_id,
            incident_generation=1,
            action_key="pause-claimed",
            action_kind="pause_ad",
            target_type="fb_ad",
            target_id="action-retirement-claimed",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        unclaimed_token = await mint_action_token(
            conn,
            recipient_id=recipient_id,
            delivery_id=int(delivery_id),
            event_id=warning.event_id,
            incident_id=incident_id,
            incident_generation=1,
            action_key="pause-unclaimed",
            action_kind="pause_ad",
            target_type="fb_ad",
            target_id="action-retirement-unclaimed",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )

    claim = await claim_action_token(
        pg_engine,
        raw_token=claimed_token.raw_token,
        chat_id=int(recipient.chat_id),
        telegram_user_id=int(recipient.telegram_user_id),
        claim_key=f"claim-before-{retirement}",
    )
    assert claim.status == "claimed"
    task_id = await create_task(
        pg_engine,
        task_type="meta_api_mutation",
        idempotency_key=f"test:claimed-action-retirement:{retirement}:{uuid.uuid4()}",
        payload={
            "mutation_kind": "pause_ad",
            "target_id": "123456789",
            "params": {},
            "ad_account_id": "987654321",
        },
        requested_by="test:claimed-action-retirement",
        max_attempts=1,
    )
    assert task_id is not None
    notification_resources.task_ids.append(task_id)

    async with pg_engine.begin() as conn:
        if retirement == "acl_revoked":
            await conn.execute(
                text("UPDATE telegram_recipients SET revoked_at = NOW() WHERE id = :id"),
                {"id": recipient_id},
            )
            await retire_revoked_recipient_backlog_in_transaction(
                conn,
                recipient_id=recipient_id,
                chat_id=int(recipient.chat_id),
            )
        else:
            await disable_recipient_delivery_in_transaction(
                conn,
                recipient_id=recipient_id,
                chat_id=int(recipient.chat_id),
            )

    assert await complete_action_token(
        pg_engine,
        token_id=claimed_token.id,
        task_id=task_id,
    )
    async with pg_engine.connect() as conn:
        tokens = (
            await conn.execute(
                text(
                    """
                    SELECT id, claimed_at, consumed_at, task_id, revoked_at
                    FROM telegram_action_tokens
                    WHERE id IN (:claimed_id, :unclaimed_id)
                    """
                ),
                {
                    "claimed_id": claimed_token.id,
                    "unclaimed_id": unclaimed_token.id,
                },
            )
        ).all()
    by_id = {uuid.UUID(str(row.id)): row for row in tokens}
    claimed_row = by_id[claimed_token.id]
    unclaimed_row = by_id[unclaimed_token.id]
    assert claimed_row.claimed_at is not None
    assert claimed_row.consumed_at is not None
    assert claimed_row.task_id == task_id
    assert claimed_row.revoked_at is None
    assert unclaimed_row.claimed_at is None
    assert unclaimed_row.consumed_at is None
    assert unclaimed_row.revoked_at is not None


@pytest.mark.asyncio
async def test_forbidden_update_reply_preserves_owner_access(
    pg_engine,
    notification_resources,
) -> None:
    recipient_id, _, _ = await _seed_recipient_and_incident(pg_engine, notification_resources)
    update_id = 8_500_000_000 + uuid.uuid4().int % 100_000_000
    notification_resources.update_ids.append(update_id)
    async with pg_engine.begin() as conn:
        recipient = (
            await conn.execute(
                text("SELECT chat_id, telegram_user_id FROM telegram_recipients WHERE id = :id"),
                {"id": recipient_id},
            )
        ).one()
        await conn.execute(
            text(
                """
                INSERT INTO telegram_updates_inbox
                    (bot_generation, update_id, payload)
                VALUES (
                    :bot_generation,
                    :update_id,
                    jsonb_build_object(
                        'update_id', CAST(:update_id AS BIGINT),
                        'callback_query', jsonb_build_object(
                            'id', 'forbidden-callback',
                            'from', jsonb_build_object(
                                'id', CAST(:telegram_user_id AS BIGINT)
                            ),
                            'message', jsonb_build_object(
                                'message_id', 1,
                                'chat', jsonb_build_object(
                                    'id', CAST(:chat_id AS BIGINT),
                                    'type', 'private'
                                )
                            ),
                            'data', 'a:redacted'
                        )
                    )
                )
                """
            ),
            {
                "bot_generation": _BOT_GENERATION,
                "update_id": update_id,
                "telegram_user_id": int(recipient.telegram_user_id),
                "chat_id": int(recipient.chat_id),
            },
        )

    claim = await claim_telegram_update(pg_engine, worker_id="forbidden-update")
    assert claim is not None and claim.update_id == update_id
    assert await mark_telegram_update_failed(
        pg_engine,
        claim=claim,
        error_code="ignored",
        gateway_error=TelegramGatewayError(
            method="answerCallbackQuery",
            kind=TelegramFailureKind.FORBIDDEN,
            error_code=403,
        ),
    )

    async with pg_engine.connect() as conn:
        state = (
            await conn.execute(
                text(
                    """
                    SELECT r.role, r.revoked_at, p.is_enabled, i.state
                    FROM telegram_recipients r
                    JOIN telegram_recipient_preferences p ON p.recipient_id = r.id
                    JOIN telegram_updates_inbox i ON i.update_id = :update_id
                    WHERE r.id = :recipient_id
                    """
                ),
                {"recipient_id": recipient_id, "update_id": update_id},
            )
        ).one()
    assert state.role == "owner"
    assert state.revoked_at is None
    assert state.is_enabled is False
    assert state.state == "dead"


@pytest.mark.asyncio
async def test_expired_edit_retries_but_expired_send_becomes_unknown(
    pg_engine,
    notification_resources,
) -> None:
    await _seed_recipient_and_incident(pg_engine, notification_resources)
    deliveries: dict[str, int] = {}
    async with pg_engine.begin() as conn:
        for operation_kind in ("edit", "send"):
            event = await enqueue_notification_in_transaction(
                conn,
                NotificationEventSpec(
                    event_type="system_delivery_acceptance",
                    severity="warning",
                    audience="owners",
                    facts=NotificationCardFacts(title=f"Expired {operation_kind}"),
                    dedupe_key=f"test:expired:{operation_kind}:{uuid.uuid4()}",
                ),
            )
            notification_resources.event_ids.append(event.event_id)
            delivery_id = await conn.scalar(
                text("SELECT id FROM notification_deliveries WHERE event_id = :event_id"),
                {"event_id": event.event_id},
            )
            deliveries[operation_kind] = int(delivery_id)
            await conn.execute(
                text(
                    """
                    UPDATE notification_deliveries
                    SET state = 'leased', lease_owner = 'crashed-worker',
                        lease_token = gen_random_uuid(),
                        lease_expires_at = NOW() - INTERVAL '1 second',
                        external_started_at = NOW() - INTERVAL '2 seconds',
                        external_operation_kind = :operation_kind
                    WHERE id = :delivery_id
                    """
                ),
                {
                    "delivery_id": delivery_id,
                    "operation_kind": operation_kind,
                },
            )

    retried, unknown = await reconcile_expired_delivery_leases(pg_engine)

    assert (retried, unknown) == (1, 1)
    async with pg_engine.connect() as conn:
        states = (
            await conn.execute(
                text(
                    "SELECT id, state FROM notification_deliveries "
                    "WHERE id = ANY(CAST(:ids AS bigint[]))"
                ),
                {"ids": list(deliveries.values())},
            )
        ).all()
    assert {int(row.id): row.state for row in states} == {
        deliveries["edit"]: "retry",
        deliveries["send"]: "unknown",
    }


@pytest.mark.asyncio
async def test_edit_token_rotation_retires_visible_capabilities_only_after_confirmed_replacement(
    pg_engine,
    notification_resources,
) -> None:
    recipient_id, incident_id, correlation_id = await _seed_recipient_and_incident(
        pg_engine,
        notification_resources,
    )

    async def enqueue_snapshot(event_type: str):
        async with pg_engine.begin() as conn:
            enqueued = await enqueue_notification_in_transaction(
                conn,
                NotificationEventSpec(
                    event_type=event_type,
                    severity="critical",
                    audience="owners",
                    facts=NotificationCardFacts(
                        title="Spend threshold",
                        open_target=NotificationNavigationTarget(
                            kind="incident",
                            target_id=str(incident_id),
                        ),
                    ),
                    actions=[
                        NotificationActionSpec(
                            key="pause",
                            label="Отключить",
                            kind="pause_ad",
                            target_type="fb_ad",
                            target_id="123456",
                        )
                    ],
                    dedupe_key=f"test:token-rotation:{event_type}:{uuid.uuid4()}",
                    incident_id=incident_id,
                    correlation_id=correlation_id,
                ),
            )
        notification_resources.event_ids.append(enqueued.event_id)
        return enqueued

    first = await enqueue_snapshot("incident_warning")
    first_claim = await claim_notification_delivery(pg_engine, worker_id="token-first")
    assert first_claim is not None and first_claim.event_id == first.event_id
    async with pg_engine.begin() as conn:
        first_token = await mint_action_token(
            conn,
            recipient_id=recipient_id,
            delivery_id=first_claim.delivery_id,
            event_id=first_claim.event_id,
            incident_id=incident_id,
            incident_generation=1,
            action_key="pause",
            action_kind="pause_ad",
            target_type="fb_ad",
            target_id="123456",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        first_navigation = await mint_navigation_token(
            conn,
            recipient_id=recipient_id,
            delivery_id=first_claim.delivery_id,
            event_id=first_claim.event_id,
            target_kind="incident",
            target_id=str(incident_id),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    assert (
        await mark_delivery_external_started(
            pg_engine,
            claim=first_claim,
            operation_kind="send",
        )
        == "ready"
    )
    assert await mark_delivery_sent(
        pg_engine,
        claim=first_claim,
        message_id=5511,
        render_hash=b"1" * 32,
        active_action_token_ids=[first_token.id],
        active_navigation_token_ids=[first_navigation.id],
    )

    second = await enqueue_snapshot("incident_warning_growth")
    second_claim = await claim_notification_delivery(pg_engine, worker_id="token-second")
    assert second_claim is not None and second_claim.event_id == second.event_id
    assert second_claim.slot_message_id == 5511
    async with pg_engine.begin() as conn:
        second_token = await mint_action_token(
            conn,
            recipient_id=recipient_id,
            delivery_id=second_claim.delivery_id,
            event_id=second_claim.event_id,
            incident_id=incident_id,
            incident_generation=1,
            action_key="pause",
            action_kind="pause_ad",
            target_type="fb_ad",
            target_id="123456",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        second_navigation = await mint_navigation_token(
            conn,
            recipient_id=recipient_id,
            delivery_id=second_claim.delivery_id,
            event_id=second_claim.event_id,
            target_kind="incident",
            target_id=str(incident_id),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    # Simulate an edit that installed ``second_navigation`` followed by a
    # worker crash before delivery finalization. A retry of the same delivery
    # must not revoke the token that can already be visible in Telegram.
    async with pg_engine.begin() as conn:
        retry_navigation = await mint_navigation_token(
            conn,
            recipient_id=recipient_id,
            delivery_id=second_claim.delivery_id,
            event_id=second_claim.event_id,
            target_kind="incident",
            target_id=str(incident_id),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    async with pg_engine.connect() as conn:
        assert (
            await conn.scalar(
                text("SELECT revoked_at FROM telegram_action_tokens WHERE id = :id"),
                {"id": first_token.id},
            )
            is None
        )
        assert (
            await conn.scalar(
                text("SELECT revoked_at FROM telegram_navigation_tokens WHERE id = :id"),
                {"id": first_navigation.id},
            )
            is None
        )
        assert (
            await conn.scalar(
                text("SELECT revoked_at FROM telegram_navigation_tokens WHERE id = :id"),
                {"id": second_navigation.id},
            )
            is None
        )

    assert (
        await mark_delivery_external_started(
            pg_engine,
            claim=second_claim,
            operation_kind="edit",
        )
        == "ready"
    )
    assert await mark_delivery_sent(
        pg_engine,
        claim=second_claim,
        message_id=5511,
        render_hash=b"2" * 32,
        active_action_token_ids=[second_token.id],
        active_navigation_token_ids=[retry_navigation.id],
    )

    async with pg_engine.connect() as conn:
        tokens = (
            await conn.execute(
                text(
                    "SELECT id, revoked_at FROM telegram_action_tokens "
                    "WHERE id IN (:first_id, :second_id)"
                ),
                {"first_id": first_token.id, "second_id": second_token.id},
            )
        ).all()
    revocations = {uuid.UUID(str(row.id)): row.revoked_at for row in tokens}
    assert revocations[first_token.id] is not None
    assert revocations[second_token.id] is None
    async with pg_engine.connect() as conn:
        navigation_tokens = (
            await conn.execute(
                text(
                    "SELECT id, revoked_at FROM telegram_navigation_tokens "
                    "WHERE id IN (:first_id, :second_id, :retry_id)"
                ),
                {
                    "first_id": first_navigation.id,
                    "second_id": second_navigation.id,
                    "retry_id": retry_navigation.id,
                },
            )
        ).all()
    navigation_revocations = {uuid.UUID(str(row.id)): row.revoked_at for row in navigation_tokens}
    assert navigation_revocations[first_navigation.id] is not None
    assert navigation_revocations[second_navigation.id] is not None
    assert navigation_revocations[retry_navigation.id] is None


@pytest.mark.asyncio
async def test_reissue_is_not_enqueued_when_supersede_cas_is_lost(
    pg_engine,
    notification_resources,
) -> None:
    recipient_id, incident_id, correlation_id = await _seed_recipient_and_incident(
        pg_engine,
        notification_resources,
    )
    warning = await _enqueue_incident_warning(
        pg_engine,
        notification_resources,
        incident_id=incident_id,
        correlation_id=correlation_id,
    )
    claim = await claim_notification_delivery(pg_engine, worker_id="stale-edit-worker")
    assert claim is not None and claim.event_id == warning.event_id
    reissue = build_incident_reissue_spec(
        source_event=claim.event,
        source_event_id=claim.event_id,
        recipient_id=claim.recipient_id,
        incident_id=incident_id,
        incident_generation=claim.incident_generation,
        incident_status=claim.incident_status,
    )
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE notification_deliveries
                SET state = 'retry', lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL
                WHERE id = :delivery_id
                """
            ),
            {"delivery_id": claim.delivery_id},
        )

    assert not await mark_delivery_superseded(
        pg_engine,
        claim=claim,
        reason="stale worker must lose",
        reissue=reissue,
    )
    async with pg_engine.connect() as conn:
        orphan_count = await conn.scalar(
            text("SELECT COUNT(*) FROM notification_events WHERE dedupe_key = :dedupe_key"),
            {"dedupe_key": reissue.dedupe_key},
        )
    assert int(orphan_count or 0) == 0


@pytest.mark.asyncio
async def test_unknown_initial_send_atomically_enqueues_explicit_reissue(
    pg_engine,
    notification_resources,
) -> None:
    recipient_id, incident_id, correlation_id = await _seed_recipient_and_incident(
        pg_engine,
        notification_resources,
    )
    warning = await _enqueue_incident_warning(
        pg_engine,
        notification_resources,
        incident_id=incident_id,
        correlation_id=correlation_id,
    )
    claim = await claim_notification_delivery(pg_engine, worker_id="unknown-initial-send")
    assert claim is not None and claim.event_id == warning.event_id
    assert (
        await mark_delivery_external_started(
            pg_engine,
            claim=claim,
            operation_kind="send",
        )
        == "ready"
    )

    decision = await mark_delivery_failure(
        pg_engine,
        claim=claim,
        error=TelegramGatewayError(
            method="sendMessage",
            kind=TelegramFailureKind.UNKNOWN,
            description="response lost after request",
        ),
    )

    assert decision.state == "unknown"
    assert decision.finalized is True
    async with pg_engine.connect() as conn:
        source_state = await conn.scalar(
            text("SELECT state FROM notification_deliveries WHERE id = :id"),
            {"id": claim.delivery_id},
        )
        reissue = (
            await conn.execute(
                text(
                    """
                    SELECT e.id, d.state
                    FROM notification_events e
                    JOIN notification_deliveries d ON d.event_id = e.id
                    WHERE e.incident_id = :incident_id
                      AND e.event_type = 'incident_snapshot_reissued'
                      AND d.recipient_id = :recipient_id
                    """
                ),
                {
                    "incident_id": incident_id,
                    "recipient_id": recipient_id,
                },
            )
        ).one()
    notification_resources.event_ids.append(uuid.UUID(str(reissue.id)))
    assert source_state == "unknown"
    assert reissue.state == "pending"

    reissue_claim = await claim_notification_delivery(pg_engine, worker_id="explicit-reissue")
    assert reissue_claim is not None
    assert reissue_claim.event.event_type == "incident_snapshot_reissued"
    assert reissue_claim.slot_message_id is None


@pytest.mark.asyncio
async def test_expired_initial_send_lease_enqueues_one_explicit_reissue(
    pg_engine,
    notification_resources,
) -> None:
    recipient_id, incident_id, correlation_id = await _seed_recipient_and_incident(
        pg_engine,
        notification_resources,
    )
    warning = await _enqueue_incident_warning(
        pg_engine,
        notification_resources,
        incident_id=incident_id,
        correlation_id=correlation_id,
    )
    claim = await claim_notification_delivery(pg_engine, worker_id="crashed-initial-send")
    assert claim is not None and claim.event_id == warning.event_id
    assert (
        await mark_delivery_external_started(
            pg_engine,
            claim=claim,
            operation_kind="send",
        )
        == "ready"
    )
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE notification_deliveries "
                "SET lease_expires_at = NOW() - INTERVAL '1 second' WHERE id = :id"
            ),
            {"id": claim.delivery_id},
        )

    retried, unknown = await reconcile_expired_delivery_leases(pg_engine)

    assert retried == 0
    assert unknown == 1
    async with pg_engine.connect() as conn:
        reissues = (
            await conn.execute(
                text(
                    """
                    SELECT e.id, d.state
                    FROM notification_events e
                    JOIN notification_deliveries d ON d.event_id = e.id
                    WHERE e.incident_id = :incident_id
                      AND e.event_type = 'incident_snapshot_reissued'
                      AND d.recipient_id = :recipient_id
                    """
                ),
                {
                    "incident_id": incident_id,
                    "recipient_id": recipient_id,
                },
            )
        ).all()
    assert len(reissues) == 1
    notification_resources.event_ids.append(uuid.UUID(str(reissues[0].id)))
    assert reissues[0].state == "pending"


@pytest.mark.asyncio
async def test_webhook_inbox_never_persists_raw_action_capability(
    pg_engine,
    notification_resources,
) -> None:
    recipient_id, incident_id, _ = await _seed_recipient_and_incident(
        pg_engine,
        notification_resources,
    )
    async with pg_engine.begin() as conn:
        issued = await mint_action_token(
            conn,
            recipient_id=recipient_id,
            incident_id=incident_id,
            incident_generation=1,
            action_key="pause",
            action_kind="pause_ad",
            target_type="fb_ad",
            target_id="ad-secret-test",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        recipient = (
            await conn.execute(
                text("SELECT chat_id, telegram_user_id FROM telegram_recipients WHERE id = :id"),
                {"id": recipient_id},
            )
        ).one()
        update_id = 8_000_000_000 + uuid.uuid4().int % 1_000_000_000
        notification_resources.update_ids.append(update_id)
        await persist_telegram_update(
            conn,
            TelegramWebhookUpdate.model_validate(
                {
                    "update_id": update_id,
                    "callback_query": {
                        "id": f"callback-{update_id}",
                        "data": issued.callback_data,
                        "from": {"id": recipient.telegram_user_id},
                        "message": {"chat": {"id": recipient.chat_id}},
                    },
                }
            ),
        )

    async with pg_engine.connect() as conn:
        inbox_text = (
            await conn.execute(
                text(
                    "SELECT row_to_json(i)::text FROM telegram_updates_inbox i "
                    "WHERE update_id = :update_id"
                ),
                {"update_id": update_id},
            )
        ).scalar_one()
        token_text = (
            await conn.execute(
                text(
                    "SELECT row_to_json(t)::text FROM telegram_action_tokens t WHERE id = :token_id"
                ),
                {"token_id": issued.id},
            )
        ).scalar_one()

    assert issued.raw_token not in inbox_text
    assert issued.raw_token not in token_text
    assert '"data": "a:redacted"' in inbox_text
    assert str(issued.id) in inbox_text


@pytest.mark.asyncio
async def test_same_incident_recipient_cannot_be_claimed_concurrently(
    pg_engine,
    notification_resources,
) -> None:
    recipient_id, incident_id, correlation_id = await _seed_recipient_and_incident(
        pg_engine, notification_resources
    )
    first_event = uuid.uuid4()
    second_event = uuid.uuid4()
    notification_resources.event_ids.extend([first_event, second_event])
    created_at = datetime.now(UTC)
    async with pg_engine.begin() as conn:
        for event_id, offset in ((first_event, 0), (second_event, 1)):
            await conn.execute(
                text(
                    """
                    INSERT INTO notification_events
                        (id, incident_id, event_type, severity, audience,
                         facts, actions, dedupe_key, correlation_id, created_at)
                    VALUES
                        (:id, :incident_id, 'incident_warning', 'warning', 'owners',
                         '{"title":"Test"}'::jsonb, '[]'::jsonb,
                         :dedupe_key, :correlation_id, :created_at)
                    """
                ),
                {
                    "id": event_id,
                    "incident_id": incident_id,
                    "dedupe_key": f"test-delivery:{event_id}",
                    "correlation_id": correlation_id,
                    "created_at": created_at + timedelta(microseconds=offset),
                },
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO notification_deliveries
                        (event_id, recipient_id, bot_generation, state,
                         scheduled_at, telegram_chat_id)
                    SELECT :event_id, :recipient_id, :bot_generation,
                           'pending', NOW(), chat_id
                    FROM telegram_recipients WHERE id = :recipient_id
                    """
                ),
                {
                    "event_id": event_id,
                    "recipient_id": recipient_id,
                    "bot_generation": _BOT_GENERATION,
                },
            )

    claims = await asyncio.gather(
        claim_notification_delivery(pg_engine, worker_id="delivery-a"),
        claim_notification_delivery(pg_engine, worker_id="delivery-b"),
    )

    assert sum(claim is not None for claim in claims) == 1
    claim = next(claim for claim in claims if claim is not None)
    assert claim.event_id == first_event


@pytest.mark.asyncio
async def test_task_transition_and_incident_notification_commit_together(
    pg_engine,
    notification_resources,
) -> None:
    _, incident_id, correlation_id = await _seed_recipient_and_incident(
        pg_engine, notification_resources
    )
    task_id = await create_task(
        pg_engine,
        task_type="meta_api_mutation",
        idempotency_key=f"incident-lifecycle:{uuid.uuid4()}",
        payload={
            "mutation_kind": "pause_ad",
            "target_id": "ad-lifecycle",
            "ad_account_id": "123",
            "params": {},
        },
        requested_by="test",
        lane="money",
        correlation_id=correlation_id,
    )
    assert task_id is not None
    notification_resources.task_ids.append(task_id)

    claimed = await claim_browser_ready_task(
        pg_engine,
        task_type="meta_api_mutation",
        lanes=("money",),
        worker_id=uuid.uuid4(),
    )
    assert claimed.task is not None
    assert claimed.task.id == task_id
    async with pg_engine.connect() as conn:
        executing = (
            await conn.execute(
                text("SELECT status FROM incidents WHERE id = :id"),
                {"id": incident_id},
            )
        ).scalar_one()
        event_id = (
            await conn.execute(
                text(
                    """
                    SELECT id FROM notification_events
                    WHERE incident_id = :id AND event_type = 'action_executing'
                    """
                ),
                {"id": incident_id},
            )
        ).scalar_one()
    notification_resources.event_ids.append(event_id)
    assert executing == "executing"

    assert await mark_succeeded(
        pg_engine,
        task_id=task_id,
        result={"outcome": "CONFIRMED"},
        lease_owner=claimed.task.lease_owner,
        lease_token=claimed.task.lease_token,
    )
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, resolved_at FROM incidents WHERE id = :id"),
                {"id": incident_id},
            )
        ).one()
        confirmed_event = (
            await conn.execute(
                text(
                    """
                    SELECT id FROM notification_events
                    WHERE incident_id = :id AND event_type = 'action_confirmed'
                    """
                ),
                {"id": incident_id},
            )
        ).scalar_one()
    notification_resources.event_ids.append(confirmed_event)
    assert row.status == "resolved"
    assert row.resolved_at is not None


@pytest.mark.asyncio
async def test_deadline_reconciler_projects_unknown_into_incident_transaction(
    pg_engine,
    notification_resources,
) -> None:
    _, incident_id, correlation_id = await _seed_recipient_and_incident(
        pg_engine, notification_resources
    )
    task_id = await create_task(
        pg_engine,
        task_type="meta_api_mutation",
        idempotency_key=f"deadline-unknown:{uuid.uuid4()}",
        payload={
            "mutation_kind": "pause_ad",
            "target_id": "ad-deadline-unknown",
            "ad_account_id": "123",
            "params": {},
        },
        requested_by="test",
        lane="money",
        correlation_id=correlation_id,
        deadline_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert task_id is not None
    notification_resources.task_ids.append(task_id)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'retrying', external_started_at = NOW(),
                    result = CAST(:result AS JSONB)
                WHERE id = :task_id
                """
            ),
            {
                "task_id": task_id,
                "result": '{"outcome":"UNKNOWN","reconcile_required":true}',
            },
        )

    assert await expire_overdue_tasks(pg_engine) == 1
    async with pg_engine.connect() as conn:
        task = (
            await conn.execute(
                text("SELECT status, result FROM task_queue WHERE id = :id"),
                {"id": task_id},
            )
        ).one()
        incident = (
            await conn.execute(
                text("SELECT status, resolved_at FROM incidents WHERE id = :id"),
                {"id": incident_id},
            )
        ).one()
        event_id = (
            await conn.execute(
                text(
                    """
                    SELECT id FROM notification_events
                    WHERE incident_id = :id AND event_type = 'action_unknown'
                    """
                ),
                {"id": incident_id},
            )
        ).scalar_one()
    notification_resources.event_ids.append(event_id)
    assert task.status == "failed"
    assert task.result["outcome"] == "UNKNOWN"
    assert incident.status == "failed"
    assert incident.resolved_at is not None


@pytest.mark.asyncio
async def test_telegram_401_gate_requires_confirmed_authentication(
    pg_engine,
    notification_resources,
) -> None:
    incident_id = uuid.uuid4()
    notification_resources.incident_ids.append(incident_id)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                DELETE FROM incidents
                WHERE incident_key = 'telegram:bot-auth'
                  AND status IN ('open','acknowledged','executing')
                """
            )
        )
        await conn.execute(
            text(
                """
                INSERT INTO incidents
                    (id, incident_key, generation, resource_type, resource_id,
                     severity, status, title, facts)
                VALUES
                    (:id, 'telegram:bot-auth', 1, 'integration', 'telegram',
                     'critical', 'open', 'Telegram auth',
                     '{"credential_fingerprint":"fingerprint-old"}'::jsonb)
                """
            ),
            {"id": incident_id},
        )

    assert not await refresh_telegram_auth_gate(
        pg_engine,
        credential_fingerprint="fingerprint-old",
    )
    assert await refresh_telegram_auth_gate(
        pg_engine,
        credential_fingerprint="fingerprint-new",
        authentication_confirmed=True,
    )
    async with pg_engine.connect() as conn:
        status = (
            await conn.execute(
                text("SELECT status FROM incidents WHERE id = :id"),
                {"id": incident_id},
            )
        ).scalar_one()
        event_ids = (
            (
                await conn.execute(
                    text("SELECT id FROM notification_events WHERE incident_id = :id"),
                    {"id": incident_id},
                )
            )
            .scalars()
            .all()
        )
    notification_resources.event_ids.extend(uuid.UUID(str(event_id)) for event_id in event_ids)
    assert status == "resolved"


@pytest.mark.asyncio
async def test_auth_incident_reopens_with_monotonic_generation_and_recovery_event(
    pg_engine,
    notification_resources,
) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                DELETE FROM notification_deliveries
                WHERE event_id IN (
                    SELECT event.id
                    FROM notification_events AS event
                    JOIN incidents AS incident ON incident.id = event.incident_id
                    WHERE incident.incident_key = 'telegram:bot-auth'
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
                    WHERE incident_key = 'telegram:bot-auth'
                )
                """
            )
        )
        await conn.execute(text("DELETE FROM incidents WHERE incident_key = 'telegram:bot-auth'"))
        assert await open_telegram_auth_incident_in_transaction(
            conn,
            error_code="telegram_unauthorized",
            credential_fingerprint="fingerprint-one",
            source="integration-test",
        )

    gateway = AsyncMock()
    gateway.credential_fingerprint = _BOT_FINGERPRINT
    gateway.get_me.side_effect = TelegramGatewayError(
        method="getMe",
        kind=TelegramFailureKind.UNAUTHORIZED,
    )
    assert not await verify_telegram_authentication(
        pg_engine,
        gateway=gateway,
        gateway_generation=_BOT_GENERATION,
    )
    async with pg_engine.connect() as conn:
        assert (
            await conn.scalar(
                text(
                    """
                    SELECT status FROM incidents
                    WHERE incident_key = 'telegram:bot-auth'
                      AND generation = 1
                    """
                )
            )
            == "open"
        )
    gateway.get_me.side_effect = None
    gateway.get_me.return_value = {"id": 1, "username": "confirmed_bot"}
    assert await verify_telegram_authentication(
        pg_engine,
        gateway=gateway,
        gateway_generation=_BOT_GENERATION,
    )
    async with pg_engine.begin() as conn:
        assert await open_telegram_auth_incident_in_transaction(
            conn,
            error_code="telegram_unauthorized",
            credential_fingerprint="fingerprint-two",
            source="integration-test",
        )

    async with pg_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT id, generation, status
                    FROM incidents
                    WHERE incident_key = 'telegram:bot-auth'
                    ORDER BY generation
                    """
                )
            )
        ).all()
        events = (
            await conn.execute(
                text(
                    """
                    SELECT event.id, event.event_type
                    FROM notification_events AS event
                    JOIN incidents AS incident ON incident.id = event.incident_id
                    WHERE incident.incident_key = 'telegram:bot-auth'
                    ORDER BY event.created_at, event.id
                    """
                )
            )
        ).all()
    notification_resources.incident_ids.extend(uuid.UUID(str(row.id)) for row in rows)
    notification_resources.event_ids.extend(uuid.UUID(str(row.id)) for row in events)
    assert [(int(row.generation), str(row.status)) for row in rows] == [
        (1, "resolved"),
        (2, "open"),
    ]
    assert [str(row.event_type) for row in events].count("incident_recovered") == 1


@pytest.mark.asyncio
async def test_auth_probe_config_update_wins_without_bot_api_call(pg_engine) -> None:
    gateway = AsyncMock()
    gateway.credential_fingerprint = _BOT_FINGERPRINT
    blocker = await pg_engine.connect()
    blocker_tx = await blocker.begin()
    probe_task = None
    try:
        await blocker.execute(
            text(
                "UPDATE telegram_config SET is_enabled = FALSE, updated_at = NOW() "
                "WHERE singleton_key = 'default'"
            )
        )
        probe_task = asyncio.create_task(
            verify_telegram_authentication(
                pg_engine,
                gateway=gateway,
                gateway_generation=_BOT_GENERATION,
            )
        )
        await _wait_for_blocked_backend(
            pg_engine,
            query_fragment="FROM telegram_config",
        )
        await blocker_tx.commit()
        assert not await probe_task
    finally:
        if blocker_tx.is_active:
            await blocker_tx.rollback()
        if probe_task is not None and not probe_task.done():
            probe_task.cancel()
            await asyncio.gather(probe_task, return_exceptions=True)
        await blocker.close()
    gateway.get_me.assert_not_awaited()


@pytest.mark.asyncio
async def test_auth_probe_external_call_wins_and_blocks_config_update(pg_engine) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def get_me() -> dict[str, object]:
        entered.set()
        await release.wait()
        return {"id": 1, "username": "confirmed_bot"}

    gateway = AsyncMock()
    gateway.credential_fingerprint = _BOT_FINGERPRINT
    gateway.get_me.side_effect = get_me

    async def disable_config() -> None:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE telegram_config SET is_enabled = FALSE, updated_at = NOW() "
                    "WHERE singleton_key = 'default'"
                )
            )

    probe_task = asyncio.create_task(
        verify_telegram_authentication(
            pg_engine,
            gateway=gateway,
            gateway_generation=_BOT_GENERATION,
        )
    )
    disable_task = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=3.0)
        disable_task = asyncio.create_task(disable_config())
        await _wait_for_blocked_backend(
            pg_engine,
            query_fragment="UPDATE telegram_config",
        )
        release.set()
        assert await probe_task
        await disable_task
    finally:
        release.set()
        for task in (probe_task, disable_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (probe_task, disable_task) if task is not None),
            return_exceptions=True,
        )

    gateway.get_me.assert_awaited_once()
    async with pg_engine.connect() as conn:
        assert not await conn.scalar(
            text("SELECT is_enabled FROM telegram_config WHERE singleton_key = 'default'")
        )


@pytest.mark.asyncio
async def test_recovery_before_warning_window_supersedes_without_orphan_send(
    pg_engine,
    notification_resources,
) -> None:
    _, incident_id, correlation_id = await _seed_recipient_and_incident(
        pg_engine, notification_resources
    )
    async with pg_engine.begin() as conn:
        warning = await enqueue_notification_in_transaction(
            conn,
            NotificationEventSpec(
                event_type="incident_warning",
                severity="warning",
                audience="owners",
                facts=NotificationCardFacts(title="Delayed warning"),
                dedupe_key=f"test:delayed-warning:{incident_id}",
                incident_id=incident_id,
                correlation_id=correlation_id,
                scheduled_at=datetime.now(UTC) + timedelta(minutes=5),
            ),
        )
    notification_resources.event_ids.append(warning.event_id)
    assert warning.delivery_count == 1

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE incidents
                SET status = 'resolved', resolved_at = NOW(), updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": incident_id},
        )
        recovery = await enqueue_notification_in_transaction(
            conn,
            NotificationEventSpec(
                event_type="incident_recovered",
                severity="ok",
                audience="owners",
                facts=NotificationCardFacts(
                    title="Delayed warning",
                    status="Восстановлено",
                ),
                dedupe_key=f"test:recovered-before-window:{incident_id}",
                incident_id=incident_id,
                correlation_id=correlation_id,
            ),
        )
    notification_resources.event_ids.append(recovery.event_id)
    assert recovery.delivery_count == 0

    async with pg_engine.connect() as conn:
        warning_delivery = (
            await conn.execute(
                text(
                    """
                    SELECT id, state FROM notification_deliveries
                    WHERE event_id = :event_id
                    """
                ),
                {"event_id": warning.event_id},
            )
        ).one()
        active_count = (
            await conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM notification_deliveries d
                    JOIN notification_events e ON e.id = d.event_id
                    WHERE e.incident_id = :incident_id
                      AND d.state IN ('pending','retry','leased')
                    """
                ),
                {"incident_id": incident_id},
            )
        ).scalar_one()
    assert warning_delivery.state == "superseded"
    assert active_count == 0
    assert await claim_notification_delivery(pg_engine, worker_id="recovery-race") is None

    # Defense in depth: even if a stale row is externally restored to pending,
    # the claim path checks the incident's current lifecycle before Telegram.
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE notification_deliveries
                SET state = 'pending', scheduled_at = NOW(), completed_at = NULL
                WHERE id = :id
                """
            ),
            {"id": warning_delivery.id},
        )
    assert await claim_notification_delivery(pg_engine, worker_id="stale-status") is None
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE notification_deliveries
                SET state = 'superseded', completed_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": warning_delivery.id},
        )


@pytest.mark.asyncio
async def test_incident_ack_is_transactional_generation_bound_and_idempotent(
    pg_engine,
    notification_resources,
) -> None:
    _, incident_id, _ = await _seed_recipient_and_incident(pg_engine, notification_resources)

    first = await acknowledge_incident(
        pg_engine,
        incident_id=incident_id,
        acknowledged_by="operator:first",
        expected_generation=1,
    )
    duplicate = await acknowledge_incident(
        pg_engine,
        incident_id=incident_id,
        acknowledged_by="operator:duplicate",
        expected_generation=1,
    )

    assert first.was_changed is True
    assert duplicate.was_changed is False
    assert duplicate.acknowledged_at == first.acknowledged_at
    assert duplicate.acknowledged_by == "operator:first"
    async with pg_engine.connect() as conn:
        incident = (
            await conn.execute(
                text("SELECT status, acknowledged_by FROM incidents WHERE id = :id"),
                {"id": incident_id},
            )
        ).one()
        events = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT id FROM notification_events
                    WHERE incident_id = :id
                      AND event_type = 'incident_acknowledged'
                    """
                    ),
                    {"id": incident_id},
                )
            )
            .scalars()
            .all()
        )
    notification_resources.event_ids.extend(events)
    assert incident.status == "acknowledged"
    assert incident.acknowledged_by == "operator:first"
    assert len(events) == 1

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE incidents
                SET status = 'open', generation = 2,
                    acknowledged_at = NULL, acknowledged_by = NULL
                WHERE id = :id
                """
            ),
            {"id": incident_id},
        )
    with pytest.raises(IncidentGenerationMismatchError):
        await acknowledge_incident(
            pg_engine,
            incident_id=incident_id,
            acknowledged_by="operator:stale",
            expected_generation=1,
        )
    async with pg_engine.connect() as conn:
        unchanged = (
            await conn.execute(
                text("SELECT status, acknowledged_at FROM incidents WHERE id = :id"),
                {"id": incident_id},
            )
        ).one()
    assert unchanged.status == "open"
    assert unchanged.acknowledged_at is None


@pytest.mark.asyncio
async def test_ack_replaces_notification_recipient_pre_boundary_snapshot(
    pg_engine,
    notification_resources,
) -> None:
    first_recipient, incident_id, correlation_id = await _seed_recipient_and_incident(
        pg_engine, notification_resources
    )
    second_recipient = uuid.uuid4()
    suffix = uuid.uuid4().int % 1_000_000_000
    notification_resources.recipient_ids.append(second_recipient)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_recipients
                    (id, chat_id, telegram_user_id, role)
                VALUES (:id, :chat_id, :user_id, 'recipient')
                """
            ),
            {
                "id": second_recipient,
                "chat_id": 7_000_000_000 + suffix,
                "user_id": 6_000_000_000 + suffix,
            },
        )
    opened = await _enqueue_incident_warning(
        pg_engine,
        notification_resources,
        incident_id=incident_id,
        correlation_id=correlation_id,
        expected_delivery_count=2,
        audience="all",
    )
    async with pg_engine.connect() as conn:
        assert (
            await conn.scalar(
                text("SELECT COUNT(*) FROM notification_deliveries WHERE event_id = :event_id"),
                {"event_id": opened.event_id},
            )
            == 2
        )

    first_claim = await claim_notification_delivery(pg_engine, worker_id="ack-owner-a")
    assert first_claim is not None
    assert first_claim.recipient_id in {first_recipient, second_recipient}
    assert (
        await mark_delivery_external_started(
            pg_engine,
            claim=first_claim,
            operation_kind="send",
        )
        == "ready"
    )
    assert await mark_delivery_sent(
        pg_engine,
        claim=first_claim,
        message_id=7711,
        render_hash=b"a" * 32,
    )

    second_claim = await claim_notification_delivery(pg_engine, worker_id="ack-owner-b")
    assert second_claim is not None
    assert second_claim.recipient_id != first_claim.recipient_id
    # Keep owner B leased before the external boundary while owner A ACKs.
    acknowledged = await acknowledge_incident(
        pg_engine,
        incident_id=incident_id,
        acknowledged_by="operator:first",
        expected_generation=1,
    )
    assert acknowledged.was_changed is True
    assert (
        await mark_delivery_external_started(
            pg_engine,
            claim=second_claim,
            operation_kind="send",
        )
        == "superseded"
    )

    async with pg_engine.connect() as conn:
        ack_event = (
            await conn.execute(
                text(
                    """
                    SELECT id
                    FROM notification_events
                    WHERE incident_id = :incident_id
                      AND event_type = 'incident_acknowledged'
                    """
                ),
                {"incident_id": incident_id},
            )
        ).one()
        notification_resources.event_ids.append(uuid.UUID(str(ack_event.id)))
        states = (
            await conn.execute(
                text(
                    """
                    SELECT recipient_id, state
                    FROM notification_deliveries
                    WHERE event_id = :event_id
                    ORDER BY recipient_id
                    """
                ),
                {"event_id": ack_event.id},
            )
        ).all()
        stale_state = await conn.scalar(
            text("SELECT state FROM notification_deliveries WHERE id = :delivery_id"),
            {"delivery_id": second_claim.delivery_id},
        )
    assert {uuid.UUID(str(row.recipient_id)) for row in states} == {
        first_recipient,
        second_recipient,
    }
    assert {str(row.state) for row in states} == {"pending"}
    assert stale_state == "superseded"


@pytest.mark.asyncio
async def test_incident_ack_callback_checks_recipient_generation_and_duplicate(
    pg_engine,
    notification_resources,
) -> None:
    recipient_id, incident_id, _ = await _seed_recipient_and_incident(
        pg_engine, notification_resources
    )
    async with pg_engine.connect() as conn:
        recipient = (
            await conn.execute(
                text("SELECT chat_id, telegram_user_id FROM telegram_recipients WHERE id = :id"),
                {"id": recipient_id},
            )
        ).one()
    async with pg_engine.begin() as conn:
        issued = await mint_action_token(
            conn,
            recipient_id=recipient_id,
            action_key="ack",
            action_kind="ack_incident",
            target_type="incident",
            target_id=str(incident_id),
            target_payload={"generation": 1},
            incident_id=incident_id,
            incident_generation=1,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )

    wrong_client = AsyncMock()
    await handle_action_callback(
        engine=pg_engine,
        client=wrong_client,
        cq_id="ack-wrong-recipient",
        raw_token=issued.raw_token,
        chat_id=int(recipient.chat_id) + 1,
        telegram_user_id=int(recipient.telegram_user_id),
        username="owner",
    )
    assert "доступ отозван" in wrong_client.answer_callback_query.await_args.kwargs["text"]
    async with pg_engine.connect() as conn:
        claimed_at = (
            await conn.execute(
                text("SELECT claimed_at FROM telegram_action_tokens WHERE id = :id"),
                {"id": issued.id},
            )
        ).scalar_one()
    assert claimed_at is None

    client = AsyncMock()
    callback_args = {
        "engine": pg_engine,
        "client": client,
        "cq_id": "ack-correct-recipient",
        "raw_token": issued.raw_token,
        "chat_id": int(recipient.chat_id),
        "telegram_user_id": int(recipient.telegram_user_id),
        "username": "owner",
    }
    await handle_action_callback(**callback_args)
    await handle_action_callback(**callback_args)

    async with pg_engine.connect() as conn:
        token = (
            await conn.execute(
                text(
                    "SELECT consumed_at, task_id, failure_code "
                    "FROM telegram_action_tokens WHERE id = :id"
                ),
                {"id": issued.id},
            )
        ).one()
        incident = (
            await conn.execute(
                text("SELECT status, acknowledged_by FROM incidents WHERE id = :id"),
                {"id": incident_id},
            )
        ).one()
        ack_events = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT id FROM notification_events
                    WHERE incident_id = :id
                      AND event_type = 'incident_acknowledged'
                    """
                    ),
                    {"id": incident_id},
                )
            )
            .scalars()
            .all()
        )
    notification_resources.event_ids.extend(ack_events)
    assert token.consumed_at is not None
    assert token.task_id is None
    assert token.failure_code is None
    assert incident.status == "acknowledged"
    assert incident.acknowledged_by == f"tg:{recipient.telegram_user_id}"
    assert len(ack_events) == 1
    assert client.answer_callback_query.await_count == 2
    assert client.answer_callback_query.await_args.kwargs["text"] == "Действие уже завершено"

    stale_incident_id = uuid.uuid4()
    notification_resources.incident_ids.append(stale_incident_id)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO incidents
                    (id, incident_key, generation, resource_type, resource_id,
                     severity, status, title)
                VALUES
                    (:id, :key, 1, 'worker', 'stale-generation',
                     'critical', 'open', 'Stale capability')
                """
            ),
            {"id": stale_incident_id, "key": f"test:{stale_incident_id}"},
        )
        stale_token = await mint_action_token(
            conn,
            recipient_id=recipient_id,
            action_key="ack",
            action_kind="ack_incident",
            target_type="incident",
            target_id=str(stale_incident_id),
            target_payload={"generation": 1},
            incident_id=stale_incident_id,
            incident_generation=1,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        await conn.execute(
            text("UPDATE incidents SET generation = 2 WHERE id = :id"),
            {"id": stale_incident_id},
        )

    stale_client = AsyncMock()
    await handle_action_callback(
        engine=pg_engine,
        client=stale_client,
        cq_id="ack-stale-generation",
        raw_token=stale_token.raw_token,
        chat_id=int(recipient.chat_id),
        telegram_user_id=int(recipient.telegram_user_id),
        username="owner",
    )
    async with pg_engine.connect() as conn:
        stale_status = (
            await conn.execute(
                text("SELECT status FROM incidents WHERE id = :id"),
                {"id": stale_incident_id},
            )
        ).scalar_one()
    assert stale_status == "open"
    assert "устарело" in stale_client.answer_callback_query.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_claimed_action_capability_retries_after_expiry_with_same_claim_key(
    pg_engine,
    notification_resources,
) -> None:
    recipient_id, incident_id, _ = await _seed_recipient_and_incident(
        pg_engine,
        notification_resources,
    )
    async with pg_engine.begin() as conn:
        recipient = (
            await conn.execute(
                text("SELECT chat_id, telegram_user_id FROM telegram_recipients WHERE id = :id"),
                {"id": recipient_id},
            )
        ).one()
        issued = await mint_action_token(
            conn,
            recipient_id=recipient_id,
            incident_id=incident_id,
            incident_generation=1,
            action_key="pause",
            action_kind="pause_ad",
            target_type="fb_ad",
            target_id="ad-expiry-retry",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )

    claim_args = {
        "engine": pg_engine,
        "token_id": issued.id,
        "chat_id": int(recipient.chat_id),
        "telegram_user_id": int(recipient.telegram_user_id),
        "claim_key": "callback-expiry-retry",
    }
    first = await claim_action_token(**claim_args)
    assert first.status == "claimed"

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE telegram_action_tokens "
                "SET expires_at = NOW() - INTERVAL '1 second' WHERE id = :id"
            ),
            {"id": issued.id},
        )

    retry = await claim_action_token(**claim_args)
    assert retry.status == "claimed"
    assert retry.token_id == issued.id
    assert await complete_action_token(
        pg_engine,
        token_id=issued.id,
        failure_code="test_terminal",
    )

    terminal_retry = await claim_action_token(**claim_args)
    assert terminal_retry.status == "already_consumed"
    assert terminal_retry.failure_code == "test_terminal"


@pytest.mark.asyncio
async def test_expired_action_capability_rejects_new_and_different_claim_keys(
    pg_engine,
    notification_resources,
) -> None:
    recipient_id, incident_id, _ = await _seed_recipient_and_incident(
        pg_engine,
        notification_resources,
    )
    async with pg_engine.begin() as conn:
        recipient = (
            await conn.execute(
                text("SELECT chat_id, telegram_user_id FROM telegram_recipients WHERE id = :id"),
                {"id": recipient_id},
            )
        ).one()
        claimed_token = await mint_action_token(
            conn,
            recipient_id=recipient_id,
            incident_id=incident_id,
            incident_generation=1,
            action_key="pause-claimed",
            action_kind="pause_ad",
            target_type="fb_ad",
            target_id="ad-expired-claimed",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        unclaimed_token = await mint_action_token(
            conn,
            recipient_id=recipient_id,
            incident_id=incident_id,
            incident_generation=1,
            action_key="pause-unclaimed",
            action_kind="pause_ad",
            target_type="fb_ad",
            target_id="ad-expired-unclaimed",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )

    identity = {
        "engine": pg_engine,
        "chat_id": int(recipient.chat_id),
        "telegram_user_id": int(recipient.telegram_user_id),
    }
    claimed = await claim_action_token(
        **identity,
        token_id=claimed_token.id,
        claim_key="original-claim",
    )
    assert claimed.status == "claimed"
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE telegram_action_tokens "
                "SET expires_at = NOW() - INTERVAL '1 second' WHERE id = :id"
            ),
            {"id": claimed_token.id},
        )

    different = await claim_action_token(
        **identity,
        token_id=claimed_token.id,
        claim_key="different-claim",
    )
    unclaimed = await claim_action_token(
        **identity,
        token_id=unclaimed_token.id,
        claim_key="new-claim",
    )

    assert different.status == "invalid"
    assert unclaimed.status == "invalid"
    async with pg_engine.connect() as conn:
        stored = (
            await conn.execute(
                text("SELECT claim_key, consumed_at FROM telegram_action_tokens WHERE id = :id"),
                {"id": claimed_token.id},
            )
        ).one()
    assert stored.claim_key == "original-claim"
    assert stored.consumed_at is None

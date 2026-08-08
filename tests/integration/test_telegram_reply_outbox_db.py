from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from core.telegram.command_replies import (
    QueuedTelegramCommandReply,
    finalize_update_with_replies,
    mark_command_reply_failure,
    reconcile_expired_command_reply_leases,
)
from core.telegram.command_replies import (
    claim_telegram_command_reply as _claim_telegram_command_reply,
)
from core.telegram.command_replies import (
    mark_command_reply_external_started as _mark_command_reply_external_started,
)
from core.telegram.gateway import (
    TelegramFailureKind,
    TelegramGatewayError,
    telegram_credential_fingerprint,
)
from core.telegram.update_inbox import ClaimedTelegramUpdate

pytestmark = pytest.mark.usefixtures("authoritative_telegram_config")

_BOT_GENERATION = 4242
_BOT_FINGERPRINT = telegram_credential_fingerprint("integration-telegram-authority-token")


async def claim_telegram_command_reply(engine, **kwargs):
    return await _claim_telegram_command_reply(
        engine,
        gateway_generation=_BOT_GENERATION,
        credential_fingerprint=_BOT_FINGERPRINT,
        **kwargs,
    )


async def mark_command_reply_external_started(engine, **kwargs):
    return await _mark_command_reply_external_started(
        engine,
        gateway_generation=_BOT_GENERATION,
        credential_fingerprint=_BOT_FINGERPRINT,
        **kwargs,
    )


def _update_id() -> int:
    return 7_000_000_000_000 + uuid.uuid4().int % 1_000_000_000_000


async def _seed_pending_reply(pg_engine, *, update_id: int, chat_id: int = 42) -> int:
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_updates_inbox
                    (bot_generation, update_id, payload, state, processed_at)
                VALUES (:bot_generation, :update_id, '{}'::jsonb,
                        'processed', NOW())
                """
            ),
            {"bot_generation": _BOT_GENERATION, "update_id": update_id},
        )
        return int(
            await conn.scalar(
                text(
                    """
                    INSERT INTO telegram_command_replies
                        (bot_generation, update_id, ordinal, chat_id, text)
                    VALUES (:bot_generation, :update_id, 0, :chat_id,
                            'authority test reply')
                    RETURNING id
                    """
                ),
                {
                    "bot_generation": _BOT_GENERATION,
                    "update_id": update_id,
                    "chat_id": chat_id,
                },
            )
        )


async def _delete_update(pg_engine, update_id: int) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM telegram_updates_inbox "
                "WHERE bot_generation = :bot_generation AND update_id = :update_id"
            ),
            {"bot_generation": _BOT_GENERATION, "update_id": update_id},
        )


@pytest.mark.asyncio
async def test_command_reply_disable_before_claim_retires_stale_generation(pg_engine) -> None:
    update_id = _update_id()
    reply_id = await _seed_pending_reply(pg_engine, update_id=update_id)
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE telegram_config SET is_enabled = FALSE, updated_at = NOW() "
                    "WHERE singleton_key = 'default'"
                )
            )

        assert (
            await claim_telegram_command_reply(
                pg_engine,
                worker_id="disabled-before-reply-claim",
            )
            is None
        )
        async with pg_engine.connect() as conn:
            state, error_code = (
                await conn.execute(
                    text(
                        "SELECT state, last_error_code FROM telegram_command_replies "
                        "WHERE id = :reply_id"
                    ),
                    {"reply_id": reply_id},
                )
            ).one()
        assert (state, error_code) == ("dead", "stale_bot_generation")
    finally:
        await _delete_update(pg_engine, update_id)


@pytest.mark.asyncio
async def test_command_reply_rotation_after_claim_fences_external_boundary(pg_engine) -> None:
    update_id = _update_id()
    await _seed_pending_reply(pg_engine, update_id=update_id)
    try:
        claim = await claim_telegram_command_reply(
            pg_engine,
            worker_id="rotate-after-reply-claim",
        )
        assert claim is not None and claim.update_id == update_id
        rotated_fingerprint = bytes.fromhex(telegram_credential_fingerprint("rotated-reply-token"))
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    UPDATE telegram_config
                    SET bot_token_encrypted = 'rotated-reply-test-token',
                        bot_token_fingerprint = :fingerprint,
                        webhook_generation = :generation,
                        webhook_applied_generation = :generation,
                        webhook_operation = 'configure',
                        webhook_state = 'configured',
                        updated_at = NOW()
                    WHERE singleton_key = 'default'
                    """
                ),
                {
                    "fingerprint": rotated_fingerprint,
                    "generation": _BOT_GENERATION + 1,
                },
            )

        assert not await mark_command_reply_external_started(pg_engine, claim=claim)
        async with pg_engine.connect() as conn:
            state, external_started_at, error_code = (
                await conn.execute(
                    text(
                        "SELECT state, external_started_at, last_error_code "
                        "FROM telegram_command_replies WHERE id = :reply_id"
                    ),
                    {"reply_id": claim.reply_id},
                )
            ).one()
        assert state == "dead"
        assert external_started_at is None
        assert error_code == "stale_bot_generation"
    finally:
        await _delete_update(pg_engine, update_id)


@pytest.mark.asyncio
async def test_stale_command_reply_401_after_rotation_does_not_open_auth_incident(
    pg_engine,
) -> None:
    update_id = _update_id()
    await _seed_pending_reply(pg_engine, update_id=update_id)
    try:
        claim = await claim_telegram_command_reply(
            pg_engine,
            worker_id="stale-reply-401",
        )
        assert claim is not None
        assert await mark_command_reply_external_started(pg_engine, claim=claim)
        rotated_fingerprint = bytes.fromhex(
            telegram_credential_fingerprint("rotated-reply-401-token")
        )
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    UPDATE telegram_config
                    SET bot_token_encrypted='rotated-reply-401-ciphertext',
                        bot_token_fingerprint=:fingerprint,
                        webhook_generation=:generation,
                        webhook_applied_generation=:generation,
                        webhook_operation='configure', webhook_state='configured',
                        updated_at=NOW()
                    WHERE singleton_key='default'
                    """
                ),
                {
                    "fingerprint": rotated_fingerprint,
                    "generation": _BOT_GENERATION + 1,
                },
            )

        decision = await mark_command_reply_failure(
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

        assert (decision.state, decision.finalized) == ("dead", True)
        async with pg_engine.connect() as conn:
            reply = (
                await conn.execute(
                    text(
                        """
                        SELECT state, last_error_code
                        FROM telegram_command_replies WHERE id=:reply_id
                        """
                    ),
                    {"reply_id": claim.reply_id},
                )
            ).one()
            auth_incidents = await conn.scalar(
                text("SELECT COUNT(*) FROM incidents WHERE incident_key='telegram:bot-auth'")
            )
        assert (reply.state, reply.last_error_code) == (
            "dead",
            "stale_bot_generation",
        )
        assert auth_incidents == 0
    finally:
        await _delete_update(pg_engine, update_id)


@pytest.mark.asyncio
async def test_current_command_reply_401_opens_auth_incident_and_retries(pg_engine) -> None:
    update_id = _update_id()
    await _seed_pending_reply(pg_engine, update_id=update_id)
    try:
        claim = await claim_telegram_command_reply(
            pg_engine,
            worker_id="current-reply-401",
        )
        assert claim is not None
        assert await mark_command_reply_external_started(pg_engine, claim=claim)
        decision = await mark_command_reply_failure(
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
        assert (decision.state, decision.finalized) == ("retry", True)
        async with pg_engine.connect() as conn:
            auth_incidents = await conn.scalar(
                text("SELECT COUNT(*) FROM incidents WHERE incident_key='telegram:bot-auth'")
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
    finally:
        await _delete_update(pg_engine, update_id)


@pytest.mark.asyncio
async def test_command_reply_cached_gateway_mismatch_cannot_claim_current_reply(
    pg_engine,
) -> None:
    update_id = _update_id()
    reply_id = await _seed_pending_reply(pg_engine, update_id=update_id)
    try:
        assert (
            await _claim_telegram_command_reply(
                pg_engine,
                worker_id="wrong-reply-generation",
                gateway_generation=_BOT_GENERATION + 1,
                credential_fingerprint=_BOT_FINGERPRINT,
            )
            is None
        )
        assert (
            await _claim_telegram_command_reply(
                pg_engine,
                worker_id="wrong-reply-fingerprint",
                gateway_generation=_BOT_GENERATION,
                credential_fingerprint="f" * 64,
            )
            is None
        )
        async with pg_engine.connect() as conn:
            assert (
                await conn.scalar(
                    text("SELECT state FROM telegram_command_replies WHERE id = :reply_id"),
                    {"reply_id": reply_id},
                )
                == "pending"
            )
    finally:
        await _delete_update(pg_engine, update_id)


@pytest.mark.asyncio
async def test_reply_intent_commits_with_processed_inbox_and_ambiguous_send_is_not_retried(
    pg_engine,
) -> None:
    update_id = _update_id()
    lease_token = uuid.uuid4()
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_updates_inbox
                        (bot_generation, update_id, payload, state,
                         attempt_count, lease_owner, lease_token,
                         lease_expires_at)
                    VALUES
                        (:bot_generation, :update_id, '{}'::jsonb,
                         'leased', 1, 'test',
                         :lease_token, NOW() + INTERVAL '5 minutes')
                    """
                ),
                {
                    "bot_generation": _BOT_GENERATION,
                    "update_id": update_id,
                    "lease_token": lease_token,
                },
            )

        claim = ClaimedTelegramUpdate(
            bot_generation=_BOT_GENERATION,
            update_id=update_id,
            payload={},
            attempt_count=1,
            lease_token=lease_token,
        )
        finalized = await finalize_update_with_replies(
            pg_engine,
            claim=claim,
            replies=(
                QueuedTelegramCommandReply(
                    ordinal=0,
                    chat_id=42,
                    text="<b>durable</b>",
                    parse_mode="HTML",
                    reply_to_message_id=7,
                    reply_markup=None,
                ),
            ),
        )
        assert finalized is True

        async with pg_engine.connect() as conn:
            inbox_state, reply_state = (
                await conn.execute(
                    text(
                        """
                        SELECT inbox.state, reply.state
                        FROM telegram_updates_inbox inbox
                        JOIN telegram_command_replies reply
                          ON reply.bot_generation = inbox.bot_generation
                         AND reply.update_id = inbox.update_id
                        WHERE inbox.bot_generation = :bot_generation
                          AND inbox.update_id = :update_id
                        """
                    ),
                    {"bot_generation": _BOT_GENERATION, "update_id": update_id},
                )
            ).one()
        assert inbox_state == "processed"
        assert reply_state == "pending"

        reply_claim = await claim_telegram_command_reply(
            pg_engine,
            worker_id="reply-test",
        )
        assert reply_claim is not None
        assert reply_claim.update_id == update_id
        assert await mark_command_reply_external_started(pg_engine, claim=reply_claim)

        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    UPDATE telegram_command_replies
                    SET lease_expires_at = NOW() - INTERVAL '1 second'
                    WHERE id = :reply_id
                    """
                ),
                {"reply_id": reply_claim.reply_id},
            )
        retried, unknown = await reconcile_expired_command_reply_leases(pg_engine)
        assert retried == 0
        assert unknown >= 1

        async with pg_engine.connect() as conn:
            state = (
                await conn.execute(
                    text("SELECT state FROM telegram_command_replies WHERE id = :reply_id"),
                    {"reply_id": reply_claim.reply_id},
                )
            ).scalar_one()
        assert state == "unknown"
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM telegram_updates_inbox "
                    "WHERE bot_generation = :bot_generation AND update_id = :update_id"
                ),
                {"bot_generation": _BOT_GENERATION, "update_id": update_id},
            )


@pytest.mark.asyncio
async def test_pre_boundary_reply_lease_is_retried(pg_engine) -> None:
    update_id = _update_id()
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_updates_inbox
                        (bot_generation, update_id, payload, state, processed_at)
                    VALUES (:bot_generation, :update_id, '{}'::jsonb,
                            'processed', NOW())
                    """
                ),
                {"bot_generation": _BOT_GENERATION, "update_id": update_id},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_command_replies
                        (bot_generation, update_id, ordinal, chat_id, text,
                         state, lease_owner, lease_token, lease_expires_at,
                         attempt_count)
                    VALUES
                        (:bot_generation, :update_id, 0, 42, 'reply',
                         'leased', 'dead-worker',
                         gen_random_uuid(), NOW() - INTERVAL '1 second', 1)
                    """
                ),
                {"bot_generation": _BOT_GENERATION, "update_id": update_id},
            )

        retried, unknown = await reconcile_expired_command_reply_leases(pg_engine)
        assert retried >= 1
        assert unknown == 0
        async with pg_engine.connect() as conn:
            state = (
                await conn.execute(
                    text("SELECT state FROM telegram_command_replies WHERE update_id = :update_id"),
                    {"update_id": update_id},
                )
            ).scalar_one()
        assert state == "retry"
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM telegram_updates_inbox "
                    "WHERE bot_generation = :bot_generation AND update_id = :update_id"
                ),
                {"bot_generation": _BOT_GENERATION, "update_id": update_id},
            )


@pytest.mark.asyncio
async def test_command_reply_rate_limit_uses_database_clock_and_full_retry_after(
    pg_engine,
) -> None:
    update_id = _update_id()
    retry_after = 137
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_updates_inbox
                        (bot_generation, update_id, payload, state, processed_at)
                    VALUES (:bot_generation, :update_id, '{}'::jsonb,
                            'processed', NOW())
                    """
                ),
                {"bot_generation": _BOT_GENERATION, "update_id": update_id},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_command_replies
                        (bot_generation, update_id, ordinal, chat_id, text)
                    VALUES (:bot_generation, :update_id, 0, 42,
                            'rate limited reply')
                    """
                ),
                {"bot_generation": _BOT_GENERATION, "update_id": update_id},
            )

        claim = await claim_telegram_command_reply(
            pg_engine,
            worker_id="rate-limited-command-reply",
        )
        assert claim is not None and claim.update_id == update_id
        assert await mark_command_reply_external_started(pg_engine, claim=claim)
        decision = await mark_command_reply_failure(
            pg_engine,
            claim=claim,
            error=TelegramGatewayError(
                method="sendMessage",
                kind=TelegramFailureKind.RATE_LIMITED,
                error_code=429,
                retry_after=retry_after,
            ),
        )

        assert decision.state == "retry"
        assert decision.finalized is True
        async with pg_engine.connect() as conn:
            state, persisted_delay = (
                await conn.execute(
                    text(
                        """
                        SELECT state,
                               EXTRACT(EPOCH FROM (scheduled_at - updated_at))
                        FROM telegram_command_replies
                        WHERE update_id = :update_id
                        """
                    ),
                    {"update_id": update_id},
                )
            ).one()
        assert state == "retry"
        assert float(persisted_delay) == retry_after
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM telegram_updates_inbox "
                    "WHERE bot_generation = :bot_generation AND update_id = :update_id"
                ),
                {"bot_generation": _BOT_GENERATION, "update_id": update_id},
            )


@pytest.mark.asyncio
async def test_forbidden_command_reply_disables_delivery_not_owner_access(pg_engine) -> None:
    update_id = _update_id()
    recipient_id = uuid.uuid4()
    chat_id = 8_600_000_000 + uuid.uuid4().int % 100_000_000
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_recipients
                        (id, chat_id, telegram_user_id, role)
                    VALUES (:recipient_id, :chat_id, :chat_id, 'owner')
                    """
                ),
                {"recipient_id": recipient_id, "chat_id": chat_id},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_updates_inbox
                        (bot_generation, update_id, payload, state, processed_at)
                    VALUES (
                        :bot_generation,
                        :update_id,
                        jsonb_build_object(
                            'update_id', CAST(:update_id AS BIGINT),
                            'message', jsonb_build_object(
                                'message_id', 1,
                                'chat', jsonb_build_object(
                                    'id', CAST(:chat_id AS BIGINT),
                                    'type', 'private'
                                ),
                                'from', jsonb_build_object(
                                    'id', CAST(:chat_id AS BIGINT)
                                ),
                                'text', '/start'
                            )
                        ),
                        'processed',
                        NOW()
                    )
                    """
                ),
                {
                    "bot_generation": _BOT_GENERATION,
                    "update_id": update_id,
                    "chat_id": chat_id,
                },
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_command_replies
                        (bot_generation, update_id, ordinal, chat_id, text)
                    VALUES (:bot_generation, :update_id, 0, :chat_id,
                            'durable reply')
                    """
                ),
                {
                    "bot_generation": _BOT_GENERATION,
                    "update_id": update_id,
                    "chat_id": chat_id,
                },
            )

        claim = await claim_telegram_command_reply(
            pg_engine,
            worker_id="forbidden-command-reply",
        )
        assert claim is not None and claim.update_id == update_id
        assert await mark_command_reply_external_started(pg_engine, claim=claim)
        decision = await mark_command_reply_failure(
            pg_engine,
            claim=claim,
            error=TelegramGatewayError(
                method="sendMessage",
                kind=TelegramFailureKind.FORBIDDEN,
                error_code=403,
            ),
        )

        assert decision.state == "dead"
        assert decision.finalized is True
        async with pg_engine.connect() as conn:
            state = (
                await conn.execute(
                    text(
                        """
                        SELECT r.role, r.revoked_at, p.is_enabled,
                               reply.state, reply.last_error_code
                        FROM telegram_recipients r
                        JOIN telegram_recipient_preferences p ON p.recipient_id = r.id
                        JOIN telegram_command_replies reply
                          ON reply.update_id = :update_id
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
        assert state.last_error_code == "telegram_forbidden"
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM telegram_updates_inbox "
                    "WHERE bot_generation = :bot_generation AND update_id = :update_id"
                ),
                {"bot_generation": _BOT_GENERATION, "update_id": update_id},
            )
            await conn.execute(
                text("DELETE FROM telegram_recipients WHERE id = :recipient_id"),
                {"recipient_id": recipient_id},
            )


@pytest.mark.asyncio
async def test_forbidden_command_reply_ignores_malformed_sender_id(pg_engine) -> None:
    update_id = _update_id()
    recipient_id = uuid.uuid4()
    chat_id = 8_700_000_000 + uuid.uuid4().int % 100_000_000
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_recipients
                        (id, chat_id, telegram_user_id, role)
                    VALUES (:recipient_id, :chat_id, :chat_id, 'owner')
                    """
                ),
                {"recipient_id": recipient_id, "chat_id": chat_id},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_updates_inbox
                        (bot_generation, update_id, payload, state, processed_at)
                    VALUES (
                        :bot_generation,
                        :update_id,
                        jsonb_build_object(
                            'update_id', CAST(:update_id AS BIGINT),
                            'message', jsonb_build_object(
                                'message_id', 1,
                                'chat', jsonb_build_object(
                                    'id', CAST(:chat_id AS BIGINT),
                                    'type', 'private'
                                ),
                                'from', jsonb_build_object('id', 'corrupt-sender-id'),
                                'text', '/start'
                            )
                        ),
                        'processed',
                        NOW()
                    )
                    """
                ),
                {
                    "bot_generation": _BOT_GENERATION,
                    "update_id": update_id,
                    "chat_id": chat_id,
                },
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_command_replies
                        (bot_generation, update_id, ordinal, chat_id, text)
                    VALUES (:bot_generation, :update_id, 0, :chat_id,
                            'durable reply')
                    """
                ),
                {
                    "bot_generation": _BOT_GENERATION,
                    "update_id": update_id,
                    "chat_id": chat_id,
                },
            )

        claim = await claim_telegram_command_reply(
            pg_engine,
            worker_id="malformed-sender-command-reply",
        )
        assert claim is not None and claim.update_id == update_id
        decision = await mark_command_reply_failure(
            pg_engine,
            claim=claim,
            error=TelegramGatewayError(
                method="sendMessage",
                kind=TelegramFailureKind.FORBIDDEN,
                error_code=403,
            ),
        )

        assert decision.state == "dead"
        assert decision.finalized is True
        async with pg_engine.connect() as conn:
            state = (
                await conn.execute(
                    text(
                        """
                        SELECT r.role, r.revoked_at, p.is_enabled, reply.state
                        FROM telegram_recipients r
                        LEFT JOIN telegram_recipient_preferences p
                          ON p.recipient_id = r.id
                        JOIN telegram_command_replies reply
                          ON reply.update_id = :update_id
                        WHERE r.id = :recipient_id
                        """
                    ),
                    {"recipient_id": recipient_id, "update_id": update_id},
                )
            ).one()
        assert state.role == "owner"
        assert state.revoked_at is None
        assert state.is_enabled is None
        assert state.state == "dead"
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM telegram_updates_inbox "
                    "WHERE bot_generation = :bot_generation AND update_id = :update_id"
                ),
                {"bot_generation": _BOT_GENERATION, "update_id": update_id},
            )
            await conn.execute(
                text("DELETE FROM telegram_recipients WHERE id = :recipient_id"),
                {"recipient_id": recipient_id},
            )

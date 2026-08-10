# -*- coding: utf-8 -*-
"""Durable outbox for deterministic Telegram command replies.

Webhook handlers write reply intents through :class:`DurableTelegramUpdateClient`.
The inbox row is marked processed in the same PostgreSQL transaction that
persists those intents.  Only the delivery worker receives the real gateway and
crosses the non-idempotent ``sendMessage`` boundary.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, TypedDict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.telegram.gateway import (
    TelegramFailureKind,
    TelegramGatewayError,
    TelegramHTMLGateway,
)
from core.telegram.notifications import (
    decide_delivery_failure,
    disable_recipient_delivery_in_transaction,
    open_telegram_auth_incident_in_transaction,
    serialize_recipient_delivery_state_in_transaction,
)
from core.telegram.outbound_authority import (
    credential_fingerprint_bytes,
    telegram_failure_authority_is_current,
)
from core.telegram.update_inbox import ClaimedTelegramUpdate


@dataclass(frozen=True)
class QueuedTelegramCommandReply:
    ordinal: int
    chat_id: int
    text: str
    parse_mode: str | None
    reply_to_message_id: int | None
    reply_markup: dict[str, Any] | None


@dataclass(frozen=True)
class ClaimedTelegramCommandReply:
    reply_id: int
    bot_generation: int
    update_id: int
    lease_token: uuid.UUID
    attempt_count: int
    max_attempts: int
    chat_id: int
    text: str
    parse_mode: str | None
    reply_to_message_id: int | None
    reply_markup: dict[str, Any] | None
    created_at: datetime


@dataclass(frozen=True)
class CommandReplyFailureDecision:
    state: Literal["retry", "dead", "unknown"]
    finalized: bool = False


class QueuedTelegramCommandReplyReceipt(TypedDict):
    """Receipt for a durable intent that has not crossed Telegram yet."""

    message_id: None
    durable: Literal[True]


class DurableTelegramUpdateClient:
    """Handler-facing client that queues sends and delegates idempotent calls.

    ``answerCallbackQuery``, menu-button changes and edits are idempotent Bot API
    operations and may use the sanitized gateway directly.  Every
    ``send_message`` call is captured in-memory until the inbox/outbox commit;
    it can never reach Telegram from a command handler.
    """

    def __init__(self, gateway: TelegramHTMLGateway) -> None:
        self._gateway = gateway
        self._replies: list[QueuedTelegramCommandReply] = []

    @property
    def replies(self) -> tuple[QueuedTelegramCommandReply, ...]:
        return tuple(self._replies)

    async def send_message(
        self,
        *,
        chat_id: int | str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = "HTML",
        reply_to_message_id: int | None = None,
        **_unused: Any,
    ) -> QueuedTelegramCommandReplyReceipt:
        try:
            normalized_chat_id = int(chat_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("chat_id must be an integer") from exc
        if normalized_chat_id == 0:
            raise ValueError("chat_id must be non-zero")
        if not text or len(text) > 4096:
            raise ValueError("Telegram reply must contain 1..4096 characters")
        if parse_mode not in (None, "HTML"):
            raise ValueError("Durable Telegram replies support HTML only")
        if reply_to_message_id is not None and int(reply_to_message_id) <= 0:
            raise ValueError("reply_to_message_id must be positive")
        self._replies.append(
            QueuedTelegramCommandReply(
                ordinal=len(self._replies),
                chat_id=normalized_chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_to_message_id=(
                    int(reply_to_message_id) if reply_to_message_id is not None else None
                ),
                reply_markup=reply_markup,
            )
        )
        # The outbox has only accepted an intent; Telegram has not assigned an
        # external message id yet.  Keep that distinction explicit and nullable.
        return {"message_id": None, "durable": True}

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str = "",
    ) -> None:
        await self._gateway.answer_callback_query(callback_query_id, text=text)

    async def set_chat_menu_button(
        self,
        *,
        web_app_url: str,
        button_text: str = "📱 Открыть",
        chat_id: int | None = None,
    ) -> None:
        await self._gateway.set_chat_menu_button(
            web_app_url=web_app_url,
            button_text=button_text,
            chat_id=chat_id,
        )


async def finalize_update_with_replies(
    engine: AsyncEngine,
    *,
    claim: ClaimedTelegramUpdate,
    replies: tuple[QueuedTelegramCommandReply, ...],
) -> bool:
    """Atomically persist all reply intents and finalize the inbox lease."""
    async with engine.begin() as conn:
        locked = (
            await conn.execute(
                text(
                    """
                    SELECT 1
                    FROM telegram_updates_inbox
                    WHERE bot_generation = :bot_generation
                      AND update_id = :update_id
                      AND state = 'leased'
                      AND lease_token = :lease_token
                      AND lease_expires_at > clock_timestamp()
                    FOR UPDATE
                    """
                ),
                {
                    "bot_generation": claim.bot_generation,
                    "update_id": claim.update_id,
                    "lease_token": claim.lease_token,
                },
            )
        ).first()
        if locked is None:
            return False

        if replies:
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_command_replies
                        (bot_generation, update_id, ordinal, chat_id, text, parse_mode,
                         reply_to_message_id, reply_markup)
                    VALUES
                        (:bot_generation, :update_id, :ordinal, :chat_id, :text, :parse_mode,
                         :reply_to_message_id, CAST(:reply_markup AS JSONB))
                    ON CONFLICT (bot_generation, update_id, ordinal) DO NOTHING
                    """
                ),
                [
                    {
                        "bot_generation": claim.bot_generation,
                        "update_id": claim.update_id,
                        "ordinal": reply.ordinal,
                        "chat_id": reply.chat_id,
                        "text": reply.text,
                        "parse_mode": reply.parse_mode,
                        "reply_to_message_id": reply.reply_to_message_id,
                        "reply_markup": (
                            json.dumps(reply.reply_markup, ensure_ascii=False)
                            if reply.reply_markup is not None
                            else None
                        ),
                    }
                    for reply in replies
                ],
            )

        finalized = await conn.execute(
            text(
                """
                UPDATE telegram_updates_inbox
                SET state = 'processed', processed_at = NOW(),
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                    last_error_code = NULL, last_error_detail = NULL
                WHERE update_id = :update_id
                  AND bot_generation = :bot_generation
                  AND state = 'leased'
                  AND lease_token = :lease_token
                """
            ),
            {
                "bot_generation": claim.bot_generation,
                "update_id": claim.update_id,
                "lease_token": claim.lease_token,
            },
        )
    return (finalized.rowcount or 0) > 0


async def claim_telegram_command_reply(
    engine: AsyncEngine,
    *,
    worker_id: str,
    gateway_generation: int,
    credential_fingerprint: str,
    lease_seconds: int = 60,
) -> ClaimedTelegramCommandReply | None:
    if not worker_id or gateway_generation <= 0 or lease_seconds < 5:
        raise ValueError("worker_id, gateway_generation and lease_seconds>=5 are required")
    credential_digest = credential_fingerprint_bytes(credential_fingerprint)
    lease_token = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE telegram_command_replies r
                SET state = 'dead', completed_at = NOW(),
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                    last_error_code = 'stale_bot_generation',
                    last_error_detail = 'Webhook generation is no longer authoritative',
                    updated_at = NOW()
                WHERE r.state IN ('pending','retry')
                  AND NOT EXISTS (
                      SELECT 1
                      FROM telegram_config c
                      WHERE c.singleton_key = 'default'
                        AND c.is_enabled
                        AND c.bot_token_encrypted <> ''
                        AND c.webhook_operation = 'configure'
                        AND c.webhook_state = 'configured'
                        AND c.webhook_applied_generation = c.webhook_generation
                        AND c.webhook_generation = r.bot_generation
                  )
                """
            )
        )
        authority = await conn.scalar(
            text(
                """
                SELECT webhook_generation
                FROM telegram_config
                WHERE singleton_key = 'default'
                  AND is_enabled
                  AND bot_token_encrypted <> ''
                  AND bot_token_fingerprint = :credential_digest
                  AND webhook_operation = 'configure'
                  AND webhook_state = 'configured'
                  AND webhook_applied_generation = webhook_generation
                  AND webhook_generation = :gateway_generation
                FOR SHARE
                """
            ),
            {
                "credential_digest": credential_digest,
                "gateway_generation": int(gateway_generation),
            },
        )
        if authority is None:
            return None
        row = (
            await conn.execute(
                text(
                    """
                    WITH candidate AS (
                        SELECT r.id
                        FROM telegram_command_replies r
                        JOIN telegram_config c
                          ON c.singleton_key = 'default'
                         AND c.is_enabled
                         AND c.bot_token_encrypted <> ''
                         AND c.webhook_operation = 'configure'
                         AND c.webhook_state = 'configured'
                         AND c.webhook_applied_generation = c.webhook_generation
                         AND c.webhook_generation = r.bot_generation
                         AND c.webhook_generation = :gateway_generation
                         AND c.bot_token_fingerprint = :credential_digest
                        WHERE r.state IN ('pending','retry')
                          AND r.scheduled_at <= NOW()
                          AND NOT EXISTS (
                              SELECT 1
                              FROM incidents auth_incident
                              WHERE auth_incident.incident_key = 'telegram:bot-auth'
                                AND auth_incident.status IN
                                    ('open','acknowledged','executing')
                          )
                        ORDER BY r.scheduled_at, r.id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE telegram_command_replies r
                    SET state = 'leased', lease_owner = :worker_id,
                        lease_token = :lease_token,
                        lease_expires_at = NOW() + make_interval(secs => :lease_seconds),
                        attempt_count = r.attempt_count + 1,
                        external_started_at = NULL,
                        updated_at = NOW()
                    FROM candidate c
                    WHERE r.id = c.id
                    RETURNING r.id, r.bot_generation, r.update_id,
                              r.lease_token, r.attempt_count,
                              r.max_attempts, r.chat_id, r.text, r.parse_mode,
                              r.reply_to_message_id, r.reply_markup, r.created_at
                    """
                ),
                {
                    "worker_id": worker_id[:96],
                    "gateway_generation": int(gateway_generation),
                    "credential_digest": credential_digest,
                    "lease_token": lease_token,
                    "lease_seconds": int(lease_seconds),
                },
            )
        ).first()
    if row is None:
        return None
    reply_markup = row[10]
    if isinstance(reply_markup, str):
        reply_markup = json.loads(reply_markup)
    return ClaimedTelegramCommandReply(
        reply_id=int(row[0]),
        bot_generation=int(row[1]),
        update_id=int(row[2]),
        lease_token=uuid.UUID(str(row[3])),
        attempt_count=int(row[4]),
        max_attempts=int(row[5]),
        chat_id=int(row[6]),
        text=str(row[7]),
        parse_mode=str(row[8]) if row[8] is not None else None,
        reply_to_message_id=int(row[9]) if row[9] is not None else None,
        reply_markup=dict(reply_markup) if reply_markup is not None else None,
        created_at=row[11],
    )


async def mark_command_reply_external_started(
    engine: AsyncEngine,
    *,
    claim: ClaimedTelegramCommandReply,
    gateway_generation: int,
    credential_fingerprint: str,
) -> bool:
    credential_digest = credential_fingerprint_bytes(credential_fingerprint)
    async with engine.begin() as conn:
        authorized = await conn.scalar(
            text(
                """
                SELECT 1
                FROM telegram_config
                WHERE singleton_key = 'default'
                  AND is_enabled
                  AND bot_token_encrypted <> ''
                  AND bot_token_fingerprint = :credential_digest
                  AND webhook_operation = 'configure'
                  AND webhook_state = 'configured'
                  AND webhook_applied_generation = webhook_generation
                  AND webhook_generation = :gateway_generation
                  AND webhook_generation = :reply_generation
                FOR SHARE
                """
            ),
            {
                "credential_digest": credential_digest,
                "gateway_generation": int(gateway_generation),
                "reply_generation": claim.bot_generation,
            },
        )
        if authorized is None:
            await conn.execute(
                text(
                    """
                    UPDATE telegram_command_replies
                    SET state = 'dead', completed_at = NOW(),
                        lease_owner = NULL, lease_token = NULL,
                        lease_expires_at = NULL,
                        last_error_code = 'stale_bot_generation',
                        last_error_detail =
                            'Telegram credential changed before external boundary',
                        updated_at = NOW()
                    WHERE id = :reply_id
                      AND state = 'leased'
                      AND lease_token = :lease_token
                      AND external_started_at IS NULL
                    """
                ),
                {"reply_id": claim.reply_id, "lease_token": claim.lease_token},
            )
            return False
        result = await conn.execute(
            text(
                """
                UPDATE telegram_command_replies
                SET external_started_at = COALESCE(external_started_at, NOW()),
                    updated_at = NOW()
                WHERE id = :reply_id
                  AND state = 'leased'
                  AND lease_token = :lease_token
                  AND lease_expires_at > clock_timestamp()
                """
            ),
            {"reply_id": claim.reply_id, "lease_token": claim.lease_token},
        )
    return (result.rowcount or 0) > 0


async def mark_command_reply_sent(
    engine: AsyncEngine,
    *,
    claim: ClaimedTelegramCommandReply,
    message_id: int,
) -> bool:
    if message_id <= 0:
        raise ValueError("message_id must be positive")
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE telegram_command_replies
                SET state = 'sent', telegram_message_id = :message_id,
                    completed_at = NOW(), lease_owner = NULL,
                    lease_token = NULL, lease_expires_at = NULL,
                    last_error_code = NULL, last_error_detail = NULL,
                    updated_at = NOW()
                WHERE id = :reply_id
                  AND state = 'leased'
                  AND lease_token = :lease_token
                  AND external_started_at IS NOT NULL
                """
            ),
            {
                "reply_id": claim.reply_id,
                "lease_token": claim.lease_token,
                "message_id": int(message_id),
            },
        )
    return (result.rowcount or 0) > 0


async def mark_command_reply_failure(
    engine: AsyncEngine,
    *,
    claim: ClaimedTelegramCommandReply,
    error: TelegramGatewayError,
    credential_fingerprint: str | None = None,
) -> CommandReplyFailureDecision:
    detail = (error.description or error.kind.value)[:500]
    async with engine.begin() as conn:
        policy_now = (await conn.execute(text("SELECT NOW()"))).scalar_one()
        policy = decide_delivery_failure(
            error,
            attempt_count=claim.attempt_count,
            max_attempts=claim.max_attempts,
            now=policy_now,
        )
        state: Literal["retry", "dead", "unknown"]
        state = policy.state if policy.state in {"retry", "dead", "unknown"} else "dead"
        error_code = policy.error_code
        auth_incident_prepared = False
        if error.kind is TelegramFailureKind.UNAUTHORIZED:
            auth_savepoint = await conn.begin_nested()
            await open_telegram_auth_incident_in_transaction(
                conn,
                error_code=policy.error_code,
                credential_fingerprint=credential_fingerprint,
                source="command_reply",
            )
            auth_incident_prepared = await telegram_failure_authority_is_current(
                conn,
                bot_generation=claim.bot_generation,
                credential_fingerprint=credential_fingerprint,
            )
            if auth_incident_prepared:
                await auth_savepoint.commit()
            else:
                await auth_savepoint.rollback()
                state = "dead"
                error_code = "stale_bot_generation"
                detail = "Telegram credential changed before 401 persistence"
        recipient = None
        if error.kind is TelegramFailureKind.FORBIDDEN:
            recipient = (
                await conn.execute(
                    text(
                        """
                        SELECT r.id, r.chat_id
                        FROM telegram_recipients r
                        JOIN telegram_updates_inbox i
                          ON i.bot_generation = :bot_generation
                         AND i.update_id = :update_id
                        WHERE r.chat_id = :chat_id
                          AND r.telegram_user_id = CASE
                              WHEN COALESCE(
                                  i.payload #>> '{callback_query,from,id}',
                                  i.payload #>> '{message,from,id}'
                              ) ~ '^[0-9]{1,16}$'
                              THEN CASE
                                  WHEN COALESCE(
                                      i.payload #>> '{callback_query,from,id}',
                                      i.payload #>> '{message,from,id}'
                                  )::BIGINT BETWEEN 1 AND 4503599627370495
                                  THEN COALESCE(
                                      i.payload #>> '{callback_query,from,id}',
                                      i.payload #>> '{message,from,id}'
                                  )::BIGINT
                                  ELSE NULL
                              END
                              ELSE NULL
                          END
                          AND r.revoked_at IS NULL
                        LIMIT 1
                        """
                    ),
                    {
                        "bot_generation": claim.bot_generation,
                        "update_id": claim.update_id,
                        "chat_id": claim.chat_id,
                    },
                )
            ).first()
            if recipient is not None:
                await serialize_recipient_delivery_state_in_transaction(
                    conn,
                    [uuid.UUID(str(recipient.id))],
                )
        result = await conn.execute(
            text(
                """
                UPDATE telegram_command_replies
                SET state = CAST(:state AS VARCHAR),
                    scheduled_at = COALESCE(:scheduled_at, scheduled_at),
                    completed_at = CASE
                                        WHEN CAST(:state AS VARCHAR) IN ('dead','unknown')
                                        THEN NOW() ELSE NULL END,
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                    last_error_code = :error_code,
                    last_error_detail = :error_detail,
                    updated_at = NOW()
                WHERE id = :reply_id
                  AND state = 'leased'
                  AND lease_token = :lease_token
                """
            ),
            {
                "reply_id": claim.reply_id,
                "lease_token": claim.lease_token,
                "state": state,
                "scheduled_at": policy.scheduled_at,
                "error_code": error_code,
                "error_detail": detail,
            },
        )
        if (result.rowcount or 0) <= 0:
            if auth_incident_prepared:
                await conn.rollback()
            return CommandReplyFailureDecision(state)

        if error.kind is TelegramFailureKind.FORBIDDEN and recipient is not None:
            await disable_recipient_delivery_in_transaction(
                conn,
                recipient_id=uuid.UUID(str(recipient.id)),
                chat_id=int(recipient.chat_id),
            )
    return CommandReplyFailureDecision(state, finalized=True)


async def reconcile_expired_command_reply_leases(engine: AsyncEngine) -> tuple[int, int]:
    """Retry pre-boundary crashes and quarantine post-boundary ambiguity."""
    async with engine.begin() as conn:
        retry_result = await conn.execute(
            text(
                """
                UPDATE telegram_command_replies
                SET state = 'retry', scheduled_at = NOW(),
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                    last_error_code = 'lease_expired_before_send',
                    last_error_detail = 'Worker lease expired before Telegram boundary',
                    updated_at = NOW()
                WHERE state = 'leased'
                  AND lease_expires_at <= NOW()
                  AND external_started_at IS NULL
                """
            )
        )
        unknown_result = await conn.execute(
            text(
                """
                UPDATE telegram_command_replies
                SET state = 'unknown', completed_at = NOW(),
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                    last_error_code = 'ambiguous_send_after_lease_expiry',
                    last_error_detail =
                        'sendMessage may have succeeded; automatic replay is forbidden',
                    updated_at = NOW()
                WHERE state = 'leased'
                  AND lease_expires_at <= NOW()
                  AND external_started_at IS NOT NULL
                """
            )
        )
    return int(retry_result.rowcount or 0), int(unknown_result.rowcount or 0)


__all__ = [
    "ClaimedTelegramCommandReply",
    "CommandReplyFailureDecision",
    "DurableTelegramUpdateClient",
    "QueuedTelegramCommandReply",
    "claim_telegram_command_reply",
    "finalize_update_with_replies",
    "mark_command_reply_external_started",
    "mark_command_reply_failure",
    "mark_command_reply_sent",
    "reconcile_expired_command_reply_leases",
]

# -*- coding: utf-8 -*-
"""Durable webhook inbox helpers for Telegram updates."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.telegram.action_tokens import digest_action_token
from core.telegram.gateway import TelegramFailureKind, TelegramGatewayError
from core.telegram.notifications import (
    disable_recipient_delivery_in_transaction,
    open_telegram_auth_incident_in_transaction,
    serialize_recipient_delivery_state_in_transaction,
)
from core.telegram.outbound_authority import telegram_failure_authority_is_current
from core.telegram.schemas import TelegramWebhookUpdate


@dataclass(frozen=True)
class ClaimedTelegramUpdate:
    bot_generation: int
    update_id: int
    payload: dict
    attempt_count: int
    lease_token: uuid.UUID


async def _normalize_callback_capability(
    conn: AsyncConnection,
    payload: dict[str, Any],
) -> None:
    """Replace a raw callback capability with its recipient-bound internal id.

    The raw 22-character value must never enter the durable inbox, logs or
    backups.  Invalid/foreign tokens are still persisted as a redacted callback
    so the worker can acknowledge them without disclosing why validation failed.
    """
    callback = payload.get("callback_query")
    if not isinstance(callback, dict):
        return
    data = callback.get("data")
    if not isinstance(data, str) or not data.startswith("a:"):
        return

    raw_token = data[2:]
    callback["data"] = "a:redacted"
    try:
        digest = digest_action_token(raw_token)
        user_id = int((callback.get("from") or {}).get("id", 0))
        chat_id = int((((callback.get("message") or {}).get("chat") or {}).get("id", 0)))
    except (AttributeError, TypeError, ValueError):
        return
    if user_id <= 0 or chat_id == 0:
        return

    row = (
        await conn.execute(
            text(
                """
                SELECT t.id
                FROM telegram_action_tokens t
                JOIN telegram_recipients r ON r.id = t.recipient_id
                WHERE t.token_digest = :digest
                  AND r.chat_id = :chat_id
                  AND r.telegram_user_id = :telegram_user_id
                LIMIT 1
                """
            ),
            {
                "digest": digest,
                "chat_id": chat_id,
                "telegram_user_id": user_id,
            },
        )
    ).first()
    if row is not None:
        callback["_fb_action_token_id"] = str(row[0])


class TelegramIngressUnavailableError(RuntimeError):
    """The requested webhook generation is not the active DB authority."""


async def persist_telegram_update(
    conn: AsyncConnection,
    update: TelegramWebhookUpdate,
    *,
    bot_generation: int,
) -> bool:
    """Insert under the exact configured bot generation before acknowledging."""
    if bot_generation <= 0:
        raise TelegramIngressUnavailableError("invalid Telegram bot generation")
    authoritative_generation = (
        await conn.execute(
            text(
                """
                SELECT webhook_generation
                FROM telegram_config
                WHERE singleton_key = 'default'
                  AND is_enabled
                  AND bot_token_encrypted <> ''
                  AND webhook_operation = 'configure'
                  AND webhook_state = 'configured'
                  AND webhook_applied_generation = webhook_generation
                  AND webhook_generation = :bot_generation
                FOR SHARE
                """
            ),
            {"bot_generation": int(bot_generation)},
        )
    ).scalar_one_or_none()
    if authoritative_generation is None:
        raise TelegramIngressUnavailableError("Telegram webhook generation is disabled or stale")
    payload = update.model_dump(mode="json", exclude_none=True)
    await _normalize_callback_capability(conn, payload)
    result = await conn.execute(
        text(
            """
            INSERT INTO telegram_updates_inbox (bot_generation, update_id, payload)
            VALUES (:bot_generation, :update_id, CAST(:payload AS JSONB))
            ON CONFLICT (bot_generation, update_id) DO NOTHING
            """
        ),
        {
            "bot_generation": int(bot_generation),
            "update_id": update.update_id,
            "payload": json.dumps(payload, ensure_ascii=False),
        },
    )
    return (result.rowcount or 0) > 0


async def claim_telegram_update(
    engine: AsyncEngine,
    *,
    worker_id: str,
    lease_seconds: int = 60,
) -> ClaimedTelegramUpdate | None:
    if not worker_id or lease_seconds < 5:
        raise ValueError("worker_id and lease_seconds>=5 are required")
    lease_token = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE telegram_updates_inbox i
                SET state = 'dead', processed_at = NOW(),
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                    last_error_code = 'stale_bot_generation',
                    last_error_detail = 'Webhook generation is no longer authoritative'
                WHERE i.state IN ('pending','retry')
                  AND NOT EXISTS (
                      SELECT 1
                      FROM telegram_config c
                      WHERE c.singleton_key = 'default'
                        AND c.is_enabled
                        AND c.bot_token_encrypted <> ''
                        AND c.webhook_operation = 'configure'
                        AND c.webhook_state = 'configured'
                        AND c.webhook_applied_generation = c.webhook_generation
                        AND c.webhook_generation = i.bot_generation
                  )
                """
            )
        )
        row = (
            await conn.execute(
                text(
                    """
                    WITH candidate AS (
                        SELECT i.bot_generation, i.update_id
                        FROM telegram_updates_inbox i
                        JOIN telegram_config c
                          ON c.singleton_key = 'default'
                         AND c.is_enabled
                         AND c.bot_token_encrypted <> ''
                         AND c.webhook_operation = 'configure'
                         AND c.webhook_state = 'configured'
                         AND c.webhook_applied_generation = c.webhook_generation
                         AND c.webhook_generation = i.bot_generation
                        WHERE i.state IN ('pending','retry')
                          AND i.scheduled_at <= NOW()
                          AND NOT EXISTS (
                              SELECT 1
                              FROM incidents auth_incident
                              WHERE auth_incident.incident_key = 'telegram:bot-auth'
                                AND auth_incident.status IN
                                    ('open','acknowledged','executing')
                          )
                        ORDER BY i.bot_generation, i.update_id
                        FOR UPDATE OF i SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE telegram_updates_inbox i
                    SET state = 'leased', lease_owner = :worker_id,
                        lease_token = :lease_token,
                        lease_expires_at = NOW() + make_interval(secs => :lease_seconds),
                        attempt_count = i.attempt_count + 1
                    FROM candidate c
                    WHERE i.bot_generation = c.bot_generation
                      AND i.update_id = c.update_id
                    RETURNING i.bot_generation, i.update_id, i.payload,
                              i.attempt_count, i.lease_token
                    """
                ),
                {
                    "worker_id": worker_id[:96],
                    "lease_token": lease_token,
                    "lease_seconds": int(lease_seconds),
                },
            )
        ).first()
    if row is None:
        return None
    payload = row[2]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return ClaimedTelegramUpdate(
        bot_generation=int(row[0]),
        update_id=int(row[1]),
        payload=dict(payload or {}),
        attempt_count=int(row[3]),
        lease_token=uuid.UUID(str(row[4])),
    )


async def telegram_update_claim_is_authoritative(
    engine: AsyncEngine,
    *,
    claim: ClaimedTelegramUpdate,
) -> bool:
    """Recheck the DB kill-switch after lease claim and before dispatch."""
    async with engine.connect() as conn:
        return bool(
            await conn.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM telegram_updates_inbox i
                        JOIN telegram_config c
                          ON c.singleton_key = 'default'
                         AND c.is_enabled
                         AND c.bot_token_encrypted <> ''
                         AND c.webhook_operation = 'configure'
                         AND c.webhook_state = 'configured'
                         AND c.webhook_applied_generation = c.webhook_generation
                         AND c.webhook_generation = i.bot_generation
                        WHERE i.bot_generation = :bot_generation
                          AND i.update_id = :update_id
                          AND i.state = 'leased'
                          AND i.lease_token = :lease_token
                          AND i.lease_expires_at > clock_timestamp()
                    )
                    """
                ),
                {
                    "bot_generation": claim.bot_generation,
                    "update_id": claim.update_id,
                    "lease_token": claim.lease_token,
                },
            )
        )


async def retire_stale_telegram_update_claim(
    engine: AsyncEngine,
    *,
    claim: ClaimedTelegramUpdate,
) -> bool:
    """Fence a leased update after disable or bot-generation rotation."""
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE telegram_updates_inbox i
                SET state = 'dead', processed_at = NOW(),
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                    last_error_code = 'stale_bot_generation',
                    last_error_detail = 'Webhook generation is no longer authoritative'
                WHERE i.bot_generation = :bot_generation
                  AND i.update_id = :update_id
                  AND i.state = 'leased'
                  AND i.lease_token = :lease_token
                  AND NOT EXISTS (
                      SELECT 1
                      FROM telegram_config c
                      WHERE c.singleton_key = 'default'
                        AND c.is_enabled
                        AND c.bot_token_encrypted <> ''
                        AND c.webhook_operation = 'configure'
                        AND c.webhook_state = 'configured'
                        AND c.webhook_applied_generation = c.webhook_generation
                        AND c.webhook_generation = i.bot_generation
                  )
                """
            ),
            {
                "bot_generation": claim.bot_generation,
                "update_id": claim.update_id,
                "lease_token": claim.lease_token,
            },
        )
    return (result.rowcount or 0) > 0


async def release_telegram_update_claim_for_gateway_refresh(
    engine: AsyncEngine,
    *,
    claim: ClaimedTelegramUpdate,
) -> bool:
    """Return an exact lease immediately when the cached gateway is stale."""
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE telegram_updates_inbox
                SET state = 'retry', scheduled_at = NOW(),
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                    last_error_code = 'gateway_generation_mismatch',
                    last_error_detail = 'Worker gateway refresh required'
                WHERE bot_generation = :bot_generation
                  AND update_id = :update_id
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
    return (result.rowcount or 0) > 0


async def mark_telegram_update_processed(
    engine: AsyncEngine,
    *,
    claim: ClaimedTelegramUpdate,
) -> bool:
    async with engine.begin() as conn:
        result = await conn.execute(
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
    return (result.rowcount or 0) > 0


async def mark_telegram_update_failed(
    engine: AsyncEngine,
    *,
    claim: ClaimedTelegramUpdate,
    error_code: str,
    error_detail: str = "",
    max_attempts: int = 5,
    gateway_error: TelegramGatewayError | None = None,
    credential_fingerprint: str | None = None,
) -> bool:
    """Persist structured Bot API policy without sleeping in worker memory."""
    is_dead = claim.attempt_count >= max_attempts
    delay = float(min(2 ** max(0, claim.attempt_count - 1), 60))
    disable_recipient_delivery = False
    open_auth_incident = False
    if gateway_error is not None:
        error_code = f"telegram_{gateway_error.kind.value}"
        error_detail = gateway_error.description or gateway_error.kind.value
        if gateway_error.kind is TelegramFailureKind.RATE_LIMITED:
            is_dead = False
            delay = float(
                gateway_error.retry_after if gateway_error.retry_after is not None else 5.0
            )
        elif gateway_error.kind is TelegramFailureKind.UNAUTHORIZED:
            is_dead = False
            delay = 300.0
            open_auth_incident = True
        elif gateway_error.kind is TelegramFailureKind.FORBIDDEN:
            is_dead = True
            disable_recipient_delivery = True
        elif gateway_error.kind in {
            TelegramFailureKind.INVALID_REQUEST,
            TelegramFailureKind.NOT_FOUND,
            TelegramFailureKind.UNKNOWN,
        }:
            # UNKNOWN sendMessage outcomes cannot be replayed without risking a
            # duplicate. Callback/edit transport failures are TRANSIENT.
            is_dead = True
    async with engine.begin() as conn:
        if open_auth_incident:
            auth_savepoint = await conn.begin_nested()
            await open_telegram_auth_incident_in_transaction(
                conn,
                error_code=error_code[:64],
                credential_fingerprint=credential_fingerprint,
                source="telegram_update",
            )
            open_auth_incident = await telegram_failure_authority_is_current(
                conn,
                bot_generation=claim.bot_generation,
                credential_fingerprint=credential_fingerprint,
            )
            if open_auth_incident:
                await auth_savepoint.commit()
            else:
                await auth_savepoint.rollback()
                is_dead = True
                error_code = "stale_bot_generation"
                error_detail = "Telegram credential changed before 401 persistence"
        recipient = None
        if disable_recipient_delivery:
            callback = claim.payload.get("callback_query") or {}
            message = callback.get("message") or {}
            chat = message.get("chat") or {}
            user = callback.get("from") or {}
            try:
                chat_id = int(chat.get("id", 0))
                telegram_user_id = int(user.get("id", 0))
            except (AttributeError, TypeError, ValueError):
                chat_id = 0
                telegram_user_id = 0
            if chat_id and telegram_user_id:
                recipient = (
                    await conn.execute(
                        text(
                            """
                            SELECT id, chat_id
                            FROM telegram_recipients
                            WHERE chat_id = :chat_id
                              AND telegram_user_id = :telegram_user_id
                            LIMIT 1
                            """
                        ),
                        {
                            "chat_id": chat_id,
                            "telegram_user_id": telegram_user_id,
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
                UPDATE telegram_updates_inbox
                SET state = CAST(:state AS VARCHAR),
                    scheduled_at = CASE WHEN CAST(:state AS VARCHAR) = 'retry'
                        THEN NOW() + make_interval(secs => :delay)
                        ELSE scheduled_at END,
                    processed_at = CASE WHEN CAST(:state AS VARCHAR) = 'dead'
                        THEN NOW() ELSE NULL END,
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                    last_error_code = :error_code,
                    last_error_detail = :error_detail
                WHERE update_id = :update_id
                  AND bot_generation = :bot_generation
                  AND state = 'leased'
                  AND lease_token = :lease_token
                """
            ),
            {
                "state": "dead" if is_dead else "retry",
                "delay": delay,
                "error_code": error_code[:64],
                "error_detail": error_detail[:500],
                "update_id": claim.update_id,
                "bot_generation": claim.bot_generation,
                "lease_token": claim.lease_token,
            },
        )
        if (result.rowcount or 0) <= 0:
            return False
        if disable_recipient_delivery and recipient is not None:
            await disable_recipient_delivery_in_transaction(
                conn,
                recipient_id=uuid.UUID(str(recipient.id)),
                chat_id=int(recipient.chat_id),
            )
    return True


async def reconcile_expired_update_leases(engine: AsyncEngine) -> int:
    async with engine.begin() as conn:
        stale = await conn.execute(
            text(
                """
                UPDATE telegram_updates_inbox i
                SET state = 'dead', processed_at = NOW(),
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                    last_error_code = 'stale_bot_generation',
                    last_error_detail = 'Webhook generation is no longer authoritative'
                WHERE i.state = 'leased'
                  AND i.lease_expires_at <= NOW()
                  AND NOT EXISTS (
                      SELECT 1
                      FROM telegram_config c
                      WHERE c.singleton_key = 'default'
                        AND c.is_enabled
                        AND c.bot_token_encrypted <> ''
                        AND c.webhook_operation = 'configure'
                        AND c.webhook_state = 'configured'
                        AND c.webhook_applied_generation = c.webhook_generation
                        AND c.webhook_generation = i.bot_generation
                  )
                """
            )
        )
        result = await conn.execute(
            text(
                """
                UPDATE telegram_updates_inbox
                SET state = 'retry', scheduled_at = NOW(),
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                    last_error_code = 'lease_expired'
                WHERE state = 'leased' AND lease_expires_at <= NOW()
                  AND EXISTS (
                      SELECT 1
                      FROM telegram_config c
                      WHERE c.singleton_key = 'default'
                        AND c.is_enabled
                        AND c.bot_token_encrypted <> ''
                        AND c.webhook_operation = 'configure'
                        AND c.webhook_state = 'configured'
                        AND c.webhook_applied_generation = c.webhook_generation
                        AND c.webhook_generation =
                            telegram_updates_inbox.bot_generation
                  )
                """
            )
        )
    return int(stale.rowcount or 0) + int(result.rowcount or 0)


__all__ = [
    "ClaimedTelegramUpdate",
    "TelegramIngressUnavailableError",
    "claim_telegram_update",
    "mark_telegram_update_failed",
    "mark_telegram_update_processed",
    "persist_telegram_update",
    "reconcile_expired_update_leases",
    "release_telegram_update_claim_for_gateway_refresh",
    "retire_stale_telegram_update_claim",
    "telegram_update_claim_is_authoritative",
]

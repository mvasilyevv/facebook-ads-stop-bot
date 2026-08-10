# -*- coding: utf-8 -*-
"""Opaque Telegram action capabilities with digest-only persistence."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

_RAW_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")


@dataclass(frozen=True)
class IssuedActionToken:
    id: uuid.UUID
    raw_token: str
    token_digest: bytes

    @property
    def callback_data(self) -> str:
        return f"a:{self.raw_token}"


@dataclass(frozen=True)
class ActionTokenClaim:
    status: Literal["claimed", "already_consumed", "invalid"]
    token_id: uuid.UUID | None = None
    action_kind: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    target_payload: dict[str, Any] | None = None
    command_idempotency_key: str | None = None
    task_id: int | None = None
    failure_code: str | None = None
    incident_id: uuid.UUID | None = None
    incident_generation: int | None = None
    correlation_id: uuid.UUID | None = None


def generate_raw_action_token() -> str:
    """Return exactly 16 random bytes encoded as 22-char unpadded Base64URL."""
    token = secrets.token_urlsafe(16)
    if not _RAW_TOKEN_RE.fullmatch(token):  # pragma: no cover - defensive stdlib contract
        raise RuntimeError("unexpected urlsafe token encoding")
    return token


def digest_action_token(raw_token: str) -> bytes:
    if not _RAW_TOKEN_RE.fullmatch(raw_token):
        raise ValueError("invalid Telegram action token")
    return hashlib.sha256(raw_token.encode("ascii")).digest()


async def mint_action_token(
    conn: AsyncConnection,
    *,
    recipient_id: uuid.UUID,
    action_key: str,
    action_kind: str,
    target_type: str,
    target_id: str,
    target_payload: dict[str, Any] | None = None,
    expires_at: datetime,
    required_role: str = "owner",
    delivery_id: int | None = None,
    event_id: uuid.UUID | None = None,
    incident_id: uuid.UUID | None = None,
    incident_generation: int | None = None,
) -> IssuedActionToken:
    """Mint a capability inside the caller's delivery transaction.

    Raw values exist only in worker memory. Minting deliberately does *not*
    revoke an older capability: until Telegram confirms the replacement edit,
    that older token may still be the only button visible to the recipient.
    Equivalent predecessors are retired atomically by
    :func:`retire_replaced_action_tokens` after delivery finalization.
    """
    if expires_at.tzinfo is None:
        raise ValueError("expires_at must be timezone-aware")
    if required_role not in {"owner", "recipient"}:
        raise ValueError("invalid required_role")
    if not action_key or not action_kind or not target_type or not target_id:
        raise ValueError("action identity must not be empty")

    raw_token = generate_raw_action_token()
    token_digest = digest_action_token(raw_token)
    row = (
        await conn.execute(
            text(
                """
                INSERT INTO telegram_action_tokens
                    (token_digest, delivery_id, event_id, incident_id, recipient_id,
                     action_key, action_kind, target_type, target_id, target_payload,
                     required_role, incident_generation, expires_at)
                VALUES
                    (:digest, :delivery_id, :event_id, :incident_id, :recipient_id,
                     :action_key, :action_kind, :target_type, :target_id,
                     CAST(:target_payload AS JSONB), :required_role,
                     :incident_generation, :expires_at)
                RETURNING id
                """
            ),
            {
                "digest": token_digest,
                "delivery_id": delivery_id,
                "event_id": event_id,
                "incident_id": incident_id,
                "recipient_id": recipient_id,
                "action_key": action_key,
                "action_kind": action_kind,
                "target_type": target_type,
                "target_id": target_id,
                "target_payload": json.dumps(target_payload or {}, ensure_ascii=False),
                "required_role": required_role,
                "incident_generation": incident_generation,
                "expires_at": expires_at,
            },
        )
    ).first()
    if row is None:  # pragma: no cover - INSERT RETURNING invariant
        raise RuntimeError("Telegram action token insert returned no id")
    return IssuedActionToken(
        id=uuid.UUID(str(row[0])),
        raw_token=raw_token,
        token_digest=token_digest,
    )


async def retire_replaced_action_tokens(
    conn: AsyncConnection,
    *,
    delivery_id: int,
    recipient_id: uuid.UUID,
    active_token_ids: Sequence[uuid.UUID],
) -> int:
    """Retire only equivalent, confirmed-replaced incident capabilities.

    The delivery lease CAS is owned by the caller. This query runs in that same
    transaction and excludes both the newly rendered tokens and any already
    claimed callback. A failed/lost Telegram edit therefore leaves the token
    currently visible in chat usable.
    """
    token_ids = tuple(active_token_ids)
    if not token_ids:
        return 0
    result = await conn.execute(
        text(
            """
            UPDATE telegram_action_tokens AS previous
            SET revoked_at = clock_timestamp()
            FROM telegram_action_tokens AS active
            WHERE active.id = ANY(CAST(:active_token_ids AS uuid[]))
              AND active.delivery_id = :delivery_id
              AND active.recipient_id = :recipient_id
              AND active.incident_id IS NOT NULL
              AND previous.id <> active.id
              AND NOT (previous.id = ANY(CAST(:active_token_ids AS uuid[])))
              AND previous.recipient_id = active.recipient_id
              AND previous.incident_id = active.incident_id
              AND previous.incident_generation
                    IS NOT DISTINCT FROM active.incident_generation
              AND previous.action_key = active.action_key
              AND previous.action_kind = active.action_kind
              AND previous.target_type = active.target_type
              AND previous.target_id = active.target_id
              AND previous.target_payload = active.target_payload
              AND previous.required_role = active.required_role
              AND previous.created_at <= active.created_at
              AND previous.claimed_at IS NULL
              AND previous.consumed_at IS NULL
              AND previous.revoked_at IS NULL
            """
        ),
        {
            "active_token_ids": list(token_ids),
            "delivery_id": int(delivery_id),
            "recipient_id": recipient_id,
        },
    )
    return int(result.rowcount or 0)


async def revoke_action_token(engine: AsyncEngine, *, token_id: uuid.UUID) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE telegram_action_tokens
                SET revoked_at = COALESCE(revoked_at, NOW())
                WHERE id = :token_id AND consumed_at IS NULL
                """
            ),
            {"token_id": token_id},
        )


async def is_claimed_action_recovery(
    engine: AsyncEngine,
    *,
    token_id: uuid.UUID,
    chat_id: int,
    telegram_user_id: int,
    claim_key: str,
) -> bool:
    """Allow only the exact durable callback to resume its prior claim."""
    if not claim_key or len(claim_key) > 128:
        return False
    async with engine.connect() as conn:
        return bool(
            await conn.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM telegram_action_tokens t
                        JOIN telegram_recipients r ON r.id = t.recipient_id
                        WHERE t.id = :token_id
                          AND r.chat_id = :chat_id
                          AND r.telegram_user_id = :telegram_user_id
                          AND t.claimed_at IS NOT NULL
                          AND t.claim_key = :claim_key
                          AND t.revoked_at IS NULL
                          AND (
                              (
                                  t.command_idempotency_key IS NOT NULL
                                  AND EXISTS (
                                      SELECT 1
                                      FROM command_idempotency_receipts receipt
                                      JOIN task_queue task
                                        ON task.id = receipt.task_id
                                      WHERE receipt.idempotency_key =
                                            t.command_idempotency_key
                                        AND receipt.action_kind = t.action_kind
                                        AND receipt.target_id = t.target_id
                                        AND task.task_type = 'meta_api_mutation'
                                  )
                              )
                              OR (
                                  t.action_kind = 'ack_incident'
                                  AND EXISTS (
                                      SELECT 1
                                      FROM incidents acknowledged
                                      WHERE acknowledged.id = t.incident_id
                                        AND acknowledged.generation =
                                            t.incident_generation
                                        AND acknowledged.acknowledged_at IS NOT NULL
                                        AND acknowledged.acknowledged_by =
                                            'tg:' || CAST(:telegram_user_id AS TEXT)
                                  )
                              )
                          )
                    )
                    """
                ),
                {
                    "token_id": token_id,
                    "chat_id": int(chat_id),
                    "telegram_user_id": int(telegram_user_id),
                    "claim_key": claim_key,
                },
            )
        )


async def claim_action_token(
    engine: AsyncEngine,
    *,
    raw_token: str | None = None,
    token_id: uuid.UUID | None = None,
    chat_id: int,
    telegram_user_id: int,
    claim_key: str,
) -> ActionTokenClaim:
    """Atomically claim a valid capability for the exact DM recipient.

    The SQL validates the capability's full incident key against the current
    ``open_state_token``. Stale buttons fail closed before any task is created.
    The raw token is never selected or persisted.
    """
    if not claim_key:
        raise ValueError("claim_key is required")
    if len(claim_key) > 128:
        raise ValueError("claim_key must not exceed 128 characters")
    if (raw_token is None) == (token_id is None):
        raise ValueError("exactly one action-token identity is required")
    if token_id is not None:
        identity_sql = "t.id = :token_id"
        identity_params: dict[str, Any] = {"token_id": token_id}
    else:
        try:
            token_digest = digest_action_token(raw_token or "")
        except ValueError:
            return ActionTokenClaim(status="invalid")
        identity_sql = "t.token_digest = :digest"
        identity_params = {"digest": token_digest}

    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    f"""
                    WITH candidate AS (
                        SELECT t.id, t.incident_id, t.incident_generation,
                               i.correlation_id
                        FROM telegram_action_tokens t
                        JOIN telegram_recipients r ON r.id = t.recipient_id
                        LEFT JOIN incidents i ON i.id = t.incident_id
                        WHERE {identity_sql}
                          AND r.chat_id = :chat_id
                          AND r.telegram_user_id = :telegram_user_id
                          AND (
                              (
                                  t.claimed_at IS NOT NULL
                                  AND t.claim_key = :claim_key
                                  AND (
                                      (
                                          r.revoked_at IS NULL
                                          AND (
                                              t.required_role <> 'owner'
                                              OR r.role = 'owner'
                                          )
                                          AND (
                                              t.incident_id IS NULL
                                              OR (
                                                  i.generation =
                                                      t.incident_generation
                                                  AND i.status IN
                                                      ('open','acknowledged','executing')
                                              )
                                          )
                                          AND (
                                              NOT (t.target_payload ? 'incident_key')
                                              OR EXISTS (
                                                  SELECT 1
                                                  FROM fb_ads a
                                                  JOIN ad_alert_state s
                                                    ON s.ad_id = a.id
                                                  WHERE a.fb_ad_id = t.target_id
                                                    AND s.open_state_token IS NOT NULL
                                                    AND s.open_state_token::text =
                                                        t.target_payload->>'incident_key'
                                              )
                                          )
                                      )
                                      OR EXISTS (
                                          SELECT 1
                                          FROM command_idempotency_receipts receipt
                                          JOIN task_queue task
                                            ON task.id = receipt.task_id
                                          WHERE receipt.idempotency_key =
                                                t.command_idempotency_key
                                            AND receipt.action_kind = t.action_kind
                                            AND receipt.target_id = t.target_id
                                            AND task.task_type =
                                                'meta_api_mutation'
                                      )
                                      OR (
                                          t.action_kind = 'ack_incident'
                                          AND EXISTS (
                                              SELECT 1
                                              FROM incidents acknowledged
                                              WHERE acknowledged.id = t.incident_id
                                                AND acknowledged.generation =
                                                    t.incident_generation
                                                AND acknowledged.acknowledged_at
                                                    IS NOT NULL
                                                AND acknowledged.acknowledged_by =
                                                    'tg:' || CAST(
                                                        :telegram_user_id AS TEXT
                                                    )
                                          )
                                      )
                                  )
                              )
                              OR (
                                  t.claimed_at IS NULL
                                  AND t.expires_at > NOW()
                                  AND r.revoked_at IS NULL
                                  AND (
                                      t.required_role <> 'owner'
                                      OR r.role = 'owner'
                                  )
                                  AND (
                                      t.incident_id IS NULL
                                      OR (
                                          i.generation = t.incident_generation
                                          AND i.status IN
                                              ('open','acknowledged','executing')
                                      )
                                  )
                                  AND (
                                      NOT (t.target_payload ? 'incident_key')
                                      OR EXISTS (
                                          SELECT 1
                                          FROM fb_ads a
                                          JOIN ad_alert_state s ON s.ad_id = a.id
                                          WHERE a.fb_ad_id = t.target_id
                                            AND s.open_state_token IS NOT NULL
                                            AND s.open_state_token::text =
                                                t.target_payload->>'incident_key'
                                      )
                                  )
                              )
                          )
                          AND t.consumed_at IS NULL
                          AND t.revoked_at IS NULL
                        FOR UPDATE OF t SKIP LOCKED
                    )
                    UPDATE telegram_action_tokens t
                    SET claimed_at = COALESCE(t.claimed_at, NOW()),
                        claim_key = COALESCE(t.claim_key, :claim_key)
                    FROM candidate c
                    WHERE t.id = c.id
                    RETURNING t.id, t.action_kind, t.target_type, t.target_id,
                              t.target_payload, t.command_idempotency_key,
                              t.task_id, t.failure_code,
                              c.incident_id, c.incident_generation,
                              c.correlation_id
                    """
                ),
                {
                    **identity_params,
                    "chat_id": int(chat_id),
                    "telegram_user_id": int(telegram_user_id),
                    "claim_key": claim_key,
                },
            )
        ).first()
        if row is not None:
            payload = row[4]
            if isinstance(payload, str):
                payload = json.loads(payload)
            return ActionTokenClaim(
                status="claimed",
                token_id=uuid.UUID(str(row[0])),
                action_kind=str(row[1]),
                target_type=str(row[2]),
                target_id=str(row[3]),
                target_payload=dict(payload or {}),
                command_idempotency_key=str(row[5]) if row[5] else None,
                task_id=int(row[6]) if row[6] is not None else None,
                failure_code=str(row[7]) if row[7] else None,
                incident_id=uuid.UUID(str(row[8])) if row[8] is not None else None,
                incident_generation=int(row[9]) if row[9] is not None else None,
                correlation_id=uuid.UUID(str(row[10])) if row[10] is not None else None,
            )

        previous = (
            await conn.execute(
                text(
                    f"""
                    SELECT t.id, t.task_id, t.failure_code
                    FROM telegram_action_tokens t
                    JOIN telegram_recipients r ON r.id = t.recipient_id
                    WHERE {identity_sql}
                      AND r.chat_id = :chat_id
                      AND r.telegram_user_id = :telegram_user_id
                      AND t.consumed_at IS NOT NULL
                    LIMIT 1
                    """
                ),
                {
                    **identity_params,
                    "chat_id": int(chat_id),
                    "telegram_user_id": int(telegram_user_id),
                },
            )
        ).first()
    if previous is None:
        return ActionTokenClaim(status="invalid")
    return ActionTokenClaim(
        status="already_consumed",
        token_id=uuid.UUID(str(previous[0])),
        task_id=int(previous[1]) if previous[1] is not None else None,
        failure_code=str(previous[2]) if previous[2] else None,
    )


async def complete_action_token(
    engine: AsyncEngine,
    *,
    token_id: uuid.UUID,
    task_id: int | None = None,
    failure_code: str | None = None,
    connection: AsyncConnection | None = None,
) -> bool:
    """Finish a claimed capability exactly once and attach its task outcome."""
    if (task_id is None) == (failure_code is None):
        raise ValueError("exactly one of task_id or failure_code is required")

    async def _complete(conn: AsyncConnection) -> bool:
        result = await conn.execute(
            text(
                """
                UPDATE telegram_action_tokens
                SET consumed_at = NOW(), task_id = :task_id,
                    failure_code = :failure_code
                WHERE id = :token_id
                  AND claimed_at IS NOT NULL
                  AND consumed_at IS NULL
                  AND revoked_at IS NULL
                """
            ),
            {
                "token_id": token_id,
                "task_id": task_id,
                "failure_code": failure_code[:64] if failure_code else None,
            },
        )
        return (result.rowcount or 0) > 0

    if connection is not None:
        return await _complete(connection)
    async with engine.begin() as conn:
        return await _complete(conn)


async def consume_action_token(
    engine: AsyncEngine,
    *,
    token_id: uuid.UUID,
    connection: AsyncConnection | None = None,
) -> bool:
    """Finish a claimed non-task capability exactly once."""

    async def _consume(conn: AsyncConnection) -> bool:
        result = await conn.execute(
            text(
                """
                UPDATE telegram_action_tokens
                SET consumed_at = NOW(), task_id = NULL, failure_code = NULL
                WHERE id = :token_id
                  AND claimed_at IS NOT NULL
                  AND consumed_at IS NULL
                  AND revoked_at IS NULL
                """
            ),
            {"token_id": token_id},
        )
        return (result.rowcount or 0) > 0

    if connection is not None:
        return await _consume(connection)
    async with engine.begin() as conn:
        return await _consume(conn)


__all__ = [
    "ActionTokenClaim",
    "IssuedActionToken",
    "claim_action_token",
    "complete_action_token",
    "consume_action_token",
    "digest_action_token",
    "generate_raw_action_token",
    "is_claimed_action_recovery",
    "mint_action_token",
    "retire_replaced_action_tokens",
    "revoke_action_token",
]

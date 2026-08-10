# -*- coding: utf-8 -*-
"""One-time opaque TMA navigation capabilities with digest-only persistence."""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

NavigationTargetKind = Literal["ad", "action", "incident"]
_RAW_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")


@dataclass(frozen=True)
class IssuedNavigationToken:
    id: uuid.UUID
    raw_token: str
    token_digest: bytes


@dataclass(frozen=True)
class NavigationTarget:
    kind: NavigationTargetKind
    target_id: str


def generate_raw_navigation_token() -> str:
    token = secrets.token_urlsafe(16)
    if not _RAW_TOKEN_RE.fullmatch(token):  # pragma: no cover - stdlib contract guard
        raise RuntimeError("unexpected urlsafe token encoding")
    return token


def digest_navigation_token(raw_token: str) -> bytes:
    if not _RAW_TOKEN_RE.fullmatch(raw_token):
        raise ValueError("invalid Telegram navigation token")
    return hashlib.sha256(raw_token.encode("ascii")).digest()


async def mint_navigation_token(
    conn: AsyncConnection,
    *,
    recipient_id: uuid.UUID,
    target_kind: NavigationTargetKind,
    target_id: str,
    expires_at: datetime,
    delivery_id: int | None = None,
    event_id: uuid.UUID | None = None,
) -> IssuedNavigationToken:
    """Mint without revoking a capability that may still be visible in chat.

    A replacement is retired only after Telegram confirms the edit/send and
    the delivery lease is finalized by :func:`retire_replaced_navigation_tokens`.
    """
    if expires_at.tzinfo is None:
        raise ValueError("expires_at must be timezone-aware")
    if target_kind not in {"ad", "action", "incident"}:
        raise ValueError("invalid navigation target kind")
    if not target_id or len(target_id) > 160:
        raise ValueError("invalid navigation target id")

    raw_token = generate_raw_navigation_token()
    token_digest = digest_navigation_token(raw_token)
    row = (
        await conn.execute(
            text(
                """
                INSERT INTO telegram_navigation_tokens
                    (token_digest, recipient_id, delivery_id, event_id,
                     target_kind, target_id, expires_at)
                VALUES
                    (:digest, :recipient_id, :delivery_id, :event_id,
                     :target_kind, :target_id, :expires_at)
                RETURNING id
                """
            ),
            {
                "digest": token_digest,
                "recipient_id": recipient_id,
                "delivery_id": delivery_id,
                "event_id": event_id,
                "target_kind": target_kind,
                "target_id": target_id,
                "expires_at": expires_at,
            },
        )
    ).first()
    if row is None:  # pragma: no cover - INSERT RETURNING invariant
        raise RuntimeError("Telegram navigation token insert returned no id")
    return IssuedNavigationToken(
        id=uuid.UUID(str(row[0])),
        raw_token=raw_token,
        token_digest=token_digest,
    )


async def retire_replaced_navigation_tokens(
    conn: AsyncConnection,
    *,
    delivery_id: int,
    recipient_id: uuid.UUID,
    active_token_ids: Sequence[uuid.UUID],
) -> int:
    """Retire navigation capabilities only after confirmed message replacement.

    Same-delivery predecessors cover retries. Incident-scoped predecessors cover
    a later lifecycle event editing the same Telegram message slot. Unrelated
    standalone messages for the same recipient remain valid.
    """
    token_ids = tuple(active_token_ids)
    if not token_ids:
        return 0
    result = await conn.execute(
        text(
            """
            UPDATE telegram_navigation_tokens AS previous
            SET revoked_at = clock_timestamp()
            FROM telegram_navigation_tokens AS active,
                 notification_events AS active_event,
                 notification_events AS previous_event
            WHERE active.id = ANY(CAST(:active_token_ids AS uuid[]))
              AND active.delivery_id = :delivery_id
              AND active.recipient_id = :recipient_id
              AND active.event_id = active_event.id
              AND previous.event_id = previous_event.id
              AND previous.id <> active.id
              AND NOT (previous.id = ANY(CAST(:active_token_ids AS uuid[])))
              AND previous.recipient_id = active.recipient_id
              AND (
                    previous.delivery_id = active.delivery_id
                    OR (
                        active_event.incident_id IS NOT NULL
                        AND previous_event.incident_id = active_event.incident_id
                    )
                  )
              AND previous.created_at <= active.created_at
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


async def consume_navigation_token(
    engine: AsyncEngine,
    *,
    raw_token: str,
    telegram_user_id: int,
) -> NavigationTarget | None:
    """Consume once and replay the same read-only target to the same recipient.

    The first UPDATE is the one-time consumption boundary. If its HTTP response
    is lost, the verified recipient may resolve the already-consumed token
    again until expiry. This is idempotent recovery of navigation, not a second
    state-changing action.
    """
    try:
        token_digest = digest_navigation_token(raw_token)
    except ValueError:
        return None

    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    WITH candidate AS (
                        SELECT t.id
                        FROM telegram_navigation_tokens t
                        JOIN telegram_recipients r ON r.id = t.recipient_id
                        WHERE t.token_digest = :digest
                          AND r.telegram_user_id = :telegram_user_id
                          AND r.revoked_at IS NULL
                          AND t.expires_at > NOW()
                          AND t.consumed_at IS NULL
                          AND t.revoked_at IS NULL
                        FOR UPDATE OF t
                    )
                    UPDATE telegram_navigation_tokens t
                    SET consumed_at = NOW()
                    FROM candidate c
                    WHERE t.id = c.id
                    RETURNING t.target_kind, t.target_id
                    """
                ),
                {"digest": token_digest, "telegram_user_id": int(telegram_user_id)},
            )
        ).first()
        if row is None:
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT t.target_kind, t.target_id
                        FROM telegram_navigation_tokens t
                        JOIN telegram_recipients r ON r.id = t.recipient_id
                        WHERE t.token_digest = :digest
                          AND r.telegram_user_id = :telegram_user_id
                          AND r.revoked_at IS NULL
                          AND t.expires_at > NOW()
                          AND t.consumed_at IS NOT NULL
                          AND t.revoked_at IS NULL
                        LIMIT 1
                        """
                    ),
                    {
                        "digest": token_digest,
                        "telegram_user_id": int(telegram_user_id),
                    },
                )
            ).first()
    if row is None:
        return None
    return NavigationTarget(kind=str(row[0]), target_id=str(row[1]))  # type: ignore[arg-type]


__all__ = [
    "IssuedNavigationToken",
    "NavigationTarget",
    "NavigationTargetKind",
    "consume_navigation_token",
    "digest_navigation_token",
    "generate_raw_navigation_token",
    "mint_navigation_token",
    "retire_replaced_navigation_tokens",
]

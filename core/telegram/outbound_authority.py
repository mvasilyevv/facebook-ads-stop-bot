# -*- coding: utf-8 -*-
"""PostgreSQL-held authority for one Telegram Bot API boundary."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


def credential_fingerprint_bytes(value: str) -> bytes:
    """Validate the public SHA-256 credential identity without retaining a token."""
    try:
        digest = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError("credential_fingerprint must be SHA-256 hex") from exc
    if len(digest) != 32:
        raise ValueError("credential_fingerprint must be SHA-256 hex")
    return digest


@asynccontextmanager
async def hold_telegram_outbound_authority(
    engine: AsyncEngine,
    *,
    bot_generation: int,
    credential_fingerprint: str,
) -> AsyncIterator[bool]:
    """Hold the config row through the actual network call.

    A token DELETE/rotation takes ``FOR UPDATE`` on the same singleton row.  If
    it commits first this yields ``False``; if this permit wins, the mutation
    cannot commit until the caller leaves the context after the Bot API call.
    """
    if bot_generation <= 0:
        raise ValueError("bot_generation must be positive")
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
                  AND webhook_generation = :bot_generation
                FOR SHARE
                """
            ),
            {
                "bot_generation": int(bot_generation),
                "credential_digest": credential_digest,
            },
        )
        yield authorized is not None


async def telegram_failure_authority_is_current(
    conn: AsyncConnection,
    *,
    bot_generation: int,
    credential_fingerprint: str | None,
) -> bool:
    """Fence delayed Bot API failures to their exact current credential.

    Failure persistence happens after the external authority context has been
    released.  Rotation may commit in that gap, so a stale 401 must only
    terminalize its own durable row and must never open the global auth gate
    for the replacement credential.  The config lock is acquired before the
    global auth-incident lock used by callers, matching every outbound path.
    """
    if bot_generation <= 0 or not credential_fingerprint:
        return False
    try:
        credential_digest = credential_fingerprint_bytes(credential_fingerprint)
    except ValueError:
        return False
    authoritative = await conn.scalar(
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
              AND webhook_generation = :bot_generation
            FOR SHARE
            """
        ),
        {
            "bot_generation": int(bot_generation),
            "credential_digest": credential_digest,
        },
    )
    return authoritative is not None


__all__ = [
    "credential_fingerprint_bytes",
    "hold_telegram_outbound_authority",
    "telegram_failure_authority_is_current",
]

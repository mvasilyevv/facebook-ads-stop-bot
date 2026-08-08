# -*- coding: utf-8 -*-
"""Transactional serialization for Telegram owner-roster mutations."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

OWNER_ROSTER_LOCK_KEY = "telegram:owner-roster:v1"


async def lock_owner_roster(connection: AsyncConnection | AsyncSession) -> None:
    """Serialize owner grants, demotions and revocations across API workers."""
    await connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": OWNER_ROSTER_LOCK_KEY},
    )


__all__ = ["OWNER_ROSTER_LOCK_KEY", "lock_owner_roster"]

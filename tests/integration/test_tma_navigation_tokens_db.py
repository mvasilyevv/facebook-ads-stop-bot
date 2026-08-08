"""Opaque TMA navigation capability replay and recipient fencing."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from core.telegram.navigation_tokens import (
    consume_navigation_token,
    mint_navigation_token,
)


@pytest.mark.asyncio
async def test_consumed_navigation_replays_only_to_same_active_recipient(pg_engine) -> None:
    recipient_id = uuid.uuid4()
    telegram_user_id = 7_800_000_001
    chat_id = 7_900_000_001
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_recipients
                        (id, chat_id, telegram_user_id, role)
                    VALUES (:id, :chat_id, :telegram_user_id, 'owner')
                    """
                ),
                {
                    "id": recipient_id,
                    "chat_id": chat_id,
                    "telegram_user_id": telegram_user_id,
                },
            )
            issued = await mint_navigation_token(
                conn,
                recipient_id=recipient_id,
                target_kind="incident",
                target_id="incident-readable-id",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )

        first = await consume_navigation_token(
            pg_engine,
            raw_token=issued.raw_token,
            telegram_user_id=telegram_user_id,
        )
        replay = await consume_navigation_token(
            pg_engine,
            raw_token=issued.raw_token,
            telegram_user_id=telegram_user_id,
        )
        wrong_user = await consume_navigation_token(
            pg_engine,
            raw_token=issued.raw_token,
            telegram_user_id=telegram_user_id + 1,
        )

        assert first is not None
        assert first.kind == "incident"
        assert first.target_id == "incident-readable-id"
        assert replay == first
        assert wrong_user is None

        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    UPDATE telegram_recipients
                    SET revoked_at = NOW()
                    WHERE id = :recipient_id
                    """
                ),
                {"recipient_id": recipient_id},
            )
        assert (
            await consume_navigation_token(
                pg_engine,
                raw_token=issued.raw_token,
                telegram_user_id=telegram_user_id,
            )
            is None
        )
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM telegram_recipients WHERE id = :recipient_id"),
                {"recipient_id": recipient_id},
            )

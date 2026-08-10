"""PostgreSQL authority for one owner-bound presentation timezone."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from core.operator.display_preferences import (
    ActiveOwnerRequiredError,
    get_operator_display_preference,
    put_operator_display_preference,
)


@pytest.mark.asyncio
async def test_owner_preference_is_provisioned_and_shared_atomically(pg_engine) -> None:
    owner_id = 8_880_000 + int(uuid.uuid4().hex[:4], 16)
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_recipients
                        (chat_id, telegram_user_id, username, role)
                    VALUES (:owner_id, :owner_id, 'display-owner', 'owner')
                    """
                ),
                {"owner_id": owner_id},
            )

        initial = await get_operator_display_preference(
            pg_engine,
            telegram_user_id=owner_id,
        )
        assert initial.timezone_name == "Europe/Kaliningrad"

        updated = await put_operator_display_preference(
            pg_engine,
            telegram_user_id=owner_id,
            timezone_name="America/New_York",
        )
        reread = await get_operator_display_preference(
            pg_engine,
            telegram_user_id=owner_id,
        )
        assert updated.timezone_name == reread.timezone_name == "America/New_York"

        async with pg_engine.connect() as conn:
            count = await conn.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM operator_display_preferences AS preference
                    JOIN telegram_recipients AS recipient
                      ON recipient.id = preference.owner_recipient_id
                    WHERE recipient.telegram_user_id = :owner_id
                    """
                ),
                {"owner_id": owner_id},
            )
        assert count == 1
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM telegram_recipients WHERE telegram_user_id = :owner_id"),
                {"owner_id": owner_id},
            )


@pytest.mark.asyncio
async def test_notification_recipient_and_revoked_owner_fail_closed(pg_engine) -> None:
    recipient_id = 8_890_000 + int(uuid.uuid4().hex[:4], 16)
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_recipients
                        (chat_id, telegram_user_id, username, role)
                    VALUES (:recipient_id, :recipient_id, 'display-recipient', 'recipient')
                    """
                ),
                {"recipient_id": recipient_id},
            )
        with pytest.raises(ActiveOwnerRequiredError):
            await get_operator_display_preference(
                pg_engine,
                telegram_user_id=recipient_id,
            )

        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE telegram_recipients SET role = 'owner', revoked_at = NOW() "
                    "WHERE telegram_user_id = :recipient_id"
                ),
                {"recipient_id": recipient_id},
            )
        with pytest.raises(ActiveOwnerRequiredError):
            await put_operator_display_preference(
                pg_engine,
                telegram_user_id=recipient_id,
                timezone_name="UTC",
            )
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM telegram_recipients WHERE telegram_user_id = :recipient_id"),
                {"recipient_id": recipient_id},
            )

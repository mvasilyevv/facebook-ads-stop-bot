# -*- coding: utf-8 -*-
"""Atomic owner-invite consumption and role propagation."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from apps.api.routers.v1.settings_telegram import delete_telegram_recipient
from core.telegram.service import consume_invite_and_create_recipient, find_active_invite


async def _create_invite(pg_engine, code: str, *, role: str) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO telegram_invites (id, code, created_by, role, expires_at) "
                "VALUES (gen_random_uuid(), :code, 'test', :role, :expires_at)"
            ),
            {
                "code": code,
                "role": role,
                "expires_at": datetime.now(timezone.utc) + timedelta(days=1),
            },
        )


async def _create_owner_invite(pg_engine, code: str) -> None:
    await _create_invite(pg_engine, code, role="owner")


async def _cleanup(pg_engine, code: str, chat_ids: tuple[int, ...]) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM telegram_recipients WHERE chat_id = ANY(:chat_ids)"),
            {"chat_ids": list(chat_ids)},
        )
        await conn.execute(
            text("DELETE FROM telegram_invites WHERE code = :code"),
            {"code": code},
        )


@pytest.mark.asyncio
async def test_owner_invite_creates_owner(pg_engine) -> None:
    code = f"OWNER{uuid.uuid4().hex[:16]}"
    chat_id = 9_990_001
    try:
        await _create_owner_invite(pg_engine, code)
        invite = await find_active_invite(pg_engine, code)
        assert invite is not None and invite["role"] == "owner"

        recipient = await consume_invite_and_create_recipient(
            pg_engine,
            code=code,
            chat_id=chat_id,
            telegram_user_id=chat_id,
            username="owner",
            display_name="Owner",
        )

        assert recipient is not None and recipient.role == "owner"
        async with pg_engine.connect() as conn:
            role = (
                await conn.execute(
                    text("SELECT role FROM telegram_recipients WHERE chat_id = :chat_id"),
                    {"chat_id": chat_id},
                )
            ).scalar_one()
        assert role == "owner"
    finally:
        await _cleanup(pg_engine, code, (chat_id,))


@pytest.mark.asyncio
async def test_consumed_invite_replays_success_only_to_same_recipient(pg_engine) -> None:
    code = f"REPLAY{uuid.uuid4().hex[:16]}"
    chat_id = 9_990_051
    other_chat_id = 9_990_052
    try:
        await _create_owner_invite(pg_engine, code)
        first = await consume_invite_and_create_recipient(
            pg_engine,
            code=code,
            chat_id=chat_id,
            telegram_user_id=chat_id,
            username="owner",
            display_name="Owner",
        )
        replay = await consume_invite_and_create_recipient(
            pg_engine,
            code=code,
            chat_id=chat_id,
            telegram_user_id=chat_id,
            username="owner",
            display_name="Owner",
        )
        stolen_replay = await consume_invite_and_create_recipient(
            pg_engine,
            code=code,
            chat_id=other_chat_id,
            telegram_user_id=other_chat_id,
            username="other",
            display_name="Other",
        )

        assert first is not None and first.role == "owner"
        assert replay == first
        assert stolen_replay is None
        async with pg_engine.connect() as conn:
            count = await conn.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM telegram_recipients
                    WHERE invite_id = (
                        SELECT id FROM telegram_invites WHERE code = :code
                    )
                    """
                ),
                {"code": code},
            )
        assert count == 1
    finally:
        await _cleanup(pg_engine, code, (chat_id, other_chat_id))


@pytest.mark.asyncio
async def test_owner_invite_has_exactly_one_concurrent_consumer(pg_engine) -> None:
    code = f"RACE{uuid.uuid4().hex[:16]}"
    chat_ids = (9_990_101, 9_990_102)
    try:
        await _create_owner_invite(pg_engine, code)

        results = await asyncio.gather(
            *(
                consume_invite_and_create_recipient(
                    pg_engine,
                    code=code,
                    chat_id=chat_id,
                    telegram_user_id=chat_id,
                    username=f"owner{chat_id}",
                    display_name="Owner",
                )
                for chat_id in chat_ids
            )
        )

        assert sum(result is not None for result in results) == 1
        async with pg_engine.connect() as conn:
            owner_count = (
                await conn.execute(
                    text(
                        "SELECT COUNT(*) FROM telegram_recipients "
                        "WHERE chat_id = ANY(:chat_ids) AND role = 'owner'"
                    ),
                    {"chat_ids": list(chat_ids)},
                )
            ).scalar_one()
        assert owner_count == 1
    finally:
        await _cleanup(pg_engine, code, chat_ids)


@pytest.mark.asyncio
async def test_recipient_invite_cannot_demote_existing_owner(pg_engine) -> None:
    code = f"RECIPIENT{uuid.uuid4().hex[:16]}"
    chat_id = 9_990_151
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_recipients
                        (chat_id, telegram_user_id, username, role)
                    VALUES (:chat_id, :chat_id, 'existing-owner', 'owner')
                    """
                ),
                {"chat_id": chat_id},
            )
        await _create_invite(pg_engine, code, role="recipient")

        recipient = await consume_invite_and_create_recipient(
            pg_engine,
            code=code,
            chat_id=chat_id,
            telegram_user_id=chat_id,
            username="updated-owner",
            display_name="Owner",
        )

        assert recipient is not None and recipient.role == "owner"
        async with pg_engine.connect() as conn:
            role = await conn.scalar(
                text("SELECT role FROM telegram_recipients WHERE chat_id = :chat_id"),
                {"chat_id": chat_id},
            )
        assert role == "owner"
    finally:
        await _cleanup(pg_engine, code, (chat_id,))


@pytest.mark.asyncio
async def test_revoked_owner_reactivates_only_with_invite_role(pg_engine) -> None:
    code = f"REACTIVATE{uuid.uuid4().hex[:16]}"
    chat_id = 9_990_152
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_recipients
                        (chat_id, telegram_user_id, username, role, revoked_at)
                    VALUES (:chat_id, :chat_id, 'revoked-owner', 'owner', NOW())
                    """
                ),
                {"chat_id": chat_id},
            )
        await _create_invite(pg_engine, code, role="recipient")

        recipient = await consume_invite_and_create_recipient(
            pg_engine,
            code=code,
            chat_id=chat_id,
            telegram_user_id=chat_id,
            username="reactivated",
            display_name="Recipient",
        )

        assert recipient is not None and recipient.role == "recipient"
        async with pg_engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT role, revoked_at FROM telegram_recipients WHERE chat_id = :chat_id"
                    ),
                    {"chat_id": chat_id},
                )
            ).one()
        assert row.role == "recipient"
        assert row.revoked_at is None
    finally:
        await _cleanup(pg_engine, code, (chat_id,))


@pytest.mark.asyncio
async def test_owner_invite_promotes_active_recipient(pg_engine) -> None:
    code = f"PROMOTE{uuid.uuid4().hex[:16]}"
    chat_id = 9_990_153
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_recipients
                        (chat_id, telegram_user_id, username, role)
                    VALUES (:chat_id, :chat_id, 'recipient', 'recipient')
                    """
                ),
                {"chat_id": chat_id},
            )
        await _create_owner_invite(pg_engine, code)

        recipient = await consume_invite_and_create_recipient(
            pg_engine,
            code=code,
            chat_id=chat_id,
            telegram_user_id=chat_id,
            username="promoted",
            display_name="Owner",
        )

        assert recipient is not None and recipient.role == "owner"
        async with pg_engine.connect() as conn:
            role = await conn.scalar(
                text("SELECT role FROM telegram_recipients WHERE chat_id = :chat_id"),
                {"chat_id": chat_id},
            )
        assert role == "owner"
    finally:
        await _cleanup(pg_engine, code, (chat_id,))


@pytest.mark.asyncio
async def test_concurrent_revoke_and_recipient_invite_preserve_an_owner(pg_engine) -> None:
    code = f"ROSTERRACE{uuid.uuid4().hex[:16]}"
    revoked_owner_id = uuid.uuid4()
    revoked_owner_chat = 9_990_154
    remaining_owner_chat = 9_990_155
    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_recipients
                        (id, chat_id, telegram_user_id, username, role)
                    VALUES
                        (:revoked_id, :revoked_chat, :revoked_chat, 'owner-a', 'owner'),
                        (gen_random_uuid(), :remaining_chat, :remaining_chat, 'owner-b', 'owner')
                    """
                ),
                {
                    "revoked_id": revoked_owner_id,
                    "revoked_chat": revoked_owner_chat,
                    "remaining_chat": remaining_owner_chat,
                },
            )
        await _create_invite(pg_engine, code, role="recipient")

        deleted, invite_result = await asyncio.gather(
            delete_telegram_recipient(str(revoked_owner_id), pg_engine),
            consume_invite_and_create_recipient(
                pg_engine,
                code=code,
                chat_id=remaining_owner_chat,
                telegram_user_id=remaining_owner_chat,
                username="owner-b-updated",
                display_name="Owner B",
            ),
        )

        assert deleted.id == str(revoked_owner_id)
        assert invite_result is not None and invite_result.role == "owner"
        async with pg_engine.connect() as conn:
            active_owner_count = await conn.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM telegram_recipients
                    WHERE chat_id = ANY(:chat_ids)
                      AND role = 'owner'
                      AND revoked_at IS NULL
                    """
                ),
                {"chat_ids": [revoked_owner_chat, remaining_owner_chat]},
            )
        assert active_owner_count == 1
    finally:
        await _cleanup(
            pg_engine,
            code,
            (revoked_owner_chat, remaining_owner_chat),
        )

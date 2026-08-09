# -*- coding: utf-8 -*-
"""Atomic owner-invite consumption and role propagation."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

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
async def test_second_owner_invite_is_not_consumed_while_owner_is_active(pg_engine) -> None:
    first_code = f"OWNER1{uuid.uuid4().hex[:16]}"
    second_code = f"OWNER2{uuid.uuid4().hex[:16]}"
    chat_ids = (9_990_111, 9_990_112)
    try:
        await _create_owner_invite(pg_engine, first_code)
        await _create_owner_invite(pg_engine, second_code)
        first = await consume_invite_and_create_recipient(
            pg_engine,
            code=first_code,
            chat_id=chat_ids[0],
            telegram_user_id=chat_ids[0],
            username="owner",
            display_name="Owner",
        )
        second = await consume_invite_and_create_recipient(
            pg_engine,
            code=second_code,
            chat_id=chat_ids[1],
            telegram_user_id=chat_ids[1],
            username="second-owner",
            display_name="Second owner",
        )

        assert first is not None and first.role == "owner"
        assert second is None
        assert await find_active_invite(pg_engine, second_code) is not None
    finally:
        await _cleanup(pg_engine, first_code, chat_ids)
        await _cleanup(pg_engine, second_code, chat_ids)


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

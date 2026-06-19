# -*- coding: utf-8 -*-
"""owner-invite → recipient с role='owner' (роль течёт из invite, не хардкод)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from core.telegram.service import consume_invite_and_create_recipient, find_active_invite


# invite с role='owner' → find_active_invite возвращает role; consume создаёт owner
@pytest.mark.asyncio
async def test_owner_invite_creates_owner(pg_engine):
    code = "OWNERCODE123"
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_recipients"))
        await conn.execute(text("DELETE FROM telegram_invites WHERE code=:c"), {"c": code})
        await conn.execute(
            text(
                "INSERT INTO telegram_invites (id, code, created_by, role, expires_at) "
                "VALUES (gen_random_uuid(), :c, 'test', 'owner', :exp)"
            ),
            {"c": code, "exp": datetime.now(timezone.utc) + timedelta(days=1)},
        )
    inv = await find_active_invite(pg_engine, code)
    assert inv is not None and inv["role"] == "owner"
    rec = await consume_invite_and_create_recipient(
        pg_engine,
        invite_id=inv["id"],
        chat_id=999,
        telegram_user_id=999,
        username="o",
        display_name="O",
        role=inv["role"],
    )
    assert rec.role == "owner"
    async with pg_engine.connect() as conn:
        role = (
            await conn.execute(text("SELECT role FROM telegram_recipients WHERE chat_id=999"))
        ).scalar()
    assert role == "owner"

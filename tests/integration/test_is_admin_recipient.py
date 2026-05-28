# -*- coding: utf-8 -*-
"""Integration: is_admin_recipient revoked_at edge cases.

HIGH #9 из backend_test_audit_round_8: is_admin_recipient проверяет роль И
revoked_at IS NULL, но граничные случаи не были покрыты:
  - role='owner' active (revoked_at IS NULL) → True
  - role='owner' revoked (revoked_at NOT NULL) → False
  - role='recipient' active → False (не owner)
  - chat_id отсутствует → False

Функция живёт в core/meta_api/queue.py и используется в approve_draft_task + ask.py.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.meta_api.queue import is_admin_recipient


@pytest_asyncio.fixture
async def clean_recipients(pg_engine: AsyncEngine):
    """Удаляет все записи telegram_recipients до и после теста."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM telegram_recipients"))

    await _truncate()
    yield
    await _truncate()


async def _add_recipient(
    engine: AsyncEngine,
    *,
    chat_id: int,
    role: str,
    revoked_at: datetime | None = None,
) -> None:
    """Добавляем запись в telegram_recipients с заданной ролью и статусом revoke."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_recipients
                    (chat_id, telegram_user_id, username, role, revoked_at)
                VALUES (:cid, :uid, :un, :role, :rev)
                """
            ),
            {
                "cid": chat_id,
                "uid": chat_id + 5000,
                "un": f"user_{chat_id}",
                "role": role,
                "rev": revoked_at,
            },
        )


# role='owner' active (revoked_at IS NULL) → True
@pytest.mark.asyncio
async def test_active_owner_returns_true(
    pg_engine: AsyncEngine,
    clean_recipients,
) -> None:
    """Активный owner (revoked_at IS NULL) должен вернуть True."""
    chat_id = 91001
    await _add_recipient(pg_engine, chat_id=chat_id, role="owner", revoked_at=None)

    result = await is_admin_recipient(pg_engine, chat_id=chat_id)
    assert result is True, "Активный owner должен пройти is_admin_recipient"


# role='owner' revoked (revoked_at IS NOT NULL) → False
@pytest.mark.asyncio
async def test_revoked_owner_returns_false(
    pg_engine: AsyncEngine,
    clean_recipients,
) -> None:
    """Отозванный owner (revoked_at IS NOT NULL) должен вернуть False."""
    chat_id = 91002
    revoked_ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    await _add_recipient(pg_engine, chat_id=chat_id, role="owner", revoked_at=revoked_ts)

    result = await is_admin_recipient(pg_engine, chat_id=chat_id)
    assert result is False, "Отозванный owner не должен пройти is_admin_recipient"


# role='recipient' active → False (не owner)
@pytest.mark.asyncio
async def test_active_recipient_role_returns_false(
    pg_engine: AsyncEngine,
    clean_recipients,
) -> None:
    """Обычный recipient (role='recipient') не является admin, даже если активен."""
    chat_id = 91003
    await _add_recipient(pg_engine, chat_id=chat_id, role="recipient", revoked_at=None)

    result = await is_admin_recipient(pg_engine, chat_id=chat_id)
    assert result is False, "Обычный recipient не должен пройти is_admin_recipient"


# chat_id отсутствует → False
@pytest.mark.asyncio
async def test_missing_chat_id_returns_false(
    pg_engine: AsyncEngine,
    clean_recipients,
) -> None:
    """Несуществующий chat_id → False (нет строки в таблице)."""
    chat_id = 99999999  # заведомо несуществующий

    result = await is_admin_recipient(pg_engine, chat_id=chat_id)
    assert result is False, "Отсутствующий chat_id должен вернуть False"

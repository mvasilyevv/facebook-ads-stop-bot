"""Isolation guard for destructive integration-test fixtures."""

from __future__ import annotations

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_session_database_lock_rejects_a_second_connection(
    pg_engine,
    _integration_test_db_session_lock,
) -> None:
    """The suite lock is session-wide, not a transaction-local test mutex."""
    lock_identity = _integration_test_db_session_lock
    if lock_identity is None:
        pytest.skip("Нет TEST_DATABASE_URL для проверки session lock")

    acquired = False
    async with pg_engine.connect() as conn:
        try:
            acquired = bool(
                await conn.scalar(
                    text("SELECT pg_try_advisory_lock(hashtextextended(:lock_identity, 0))"),
                    {"lock_identity": lock_identity},
                )
            )
            assert acquired is False
        finally:
            if acquired:
                await conn.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:lock_identity, 0))"),
                    {"lock_identity": lock_identity},
                )

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.observer.cabinet_supervisor import publish_next_scan_at


def _engine(*, rowcount: int = 1) -> tuple[MagicMock, AsyncMock]:
    connection = AsyncMock()
    connection.execute.return_value = SimpleNamespace(rowcount=rowcount)
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=None)
    engine = MagicMock()
    engine.begin.return_value = transaction
    return engine, connection


@pytest.mark.asyncio
async def test_publish_next_scan_at_uses_canonical_unique_accounts_and_idle_guard() -> None:
    engine, connection = _engine(rowcount=2)
    scheduled_at = datetime(2026, 8, 9, 12, 30, tzinfo=UTC)

    updated = await publish_next_scan_at(
        engine,
        ad_account_ids=["act_222", "111", "222"],
        next_scan_at=scheduled_at,
    )

    assert updated == 2
    statement, params = connection.execute.await_args.args
    sql = str(statement)
    assert "owner_instance IS NULL" in sql
    assert params == {
        "accounts": ["111", "222"],
        "next_scan_at": scheduled_at,
    }


@pytest.mark.asyncio
async def test_publish_next_scan_at_rejects_naive_time_without_database_write() -> None:
    engine, connection = _engine()

    with pytest.raises(ValueError, match="timezone-aware"):
        await publish_next_scan_at(
            engine,
            ad_account_ids=["111"],
            next_scan_at=datetime(2026, 8, 9, 12, 30),
        )

    connection.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_next_scan_at_skips_empty_cabinet_set() -> None:
    engine, connection = _engine()

    updated = await publish_next_scan_at(
        engine,
        ad_account_ids=[],
        next_scan_at=datetime(2026, 8, 9, 12, 30, tzinfo=UTC),
    )

    assert updated == 0
    connection.execute.assert_not_awaited()

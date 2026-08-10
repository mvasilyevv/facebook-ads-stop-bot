from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from core.meta_api.bulk import locked_status_targets


class _StatusLockConnection:
    def __init__(self, *, busy_ad_id: str | None = None) -> None:
        self.busy_ad_id = busy_ad_id
        self.try_calls: list[str] = []
        self.unlock_calls: list[str] = []
        self.commits = 0
        self.invalidated = False

    async def scalar(self, statement: object, params: dict[str, str]) -> bool:
        sql = str(statement)
        ad_id = params["ad_id"]
        if "pg_try_advisory_lock" in sql:
            self.try_calls.append(ad_id)
            return ad_id != self.busy_ad_id
        if "pg_advisory_unlock" in sql:
            self.unlock_calls.append(ad_id)
            return True
        raise AssertionError(f"unexpected lock SQL: {sql}")

    async def commit(self) -> None:
        self.commits += 1

    def in_transaction(self) -> bool:
        return False

    async def rollback(self) -> None:
        raise AssertionError("rollback is not expected without an open transaction")

    async def invalidate(self) -> None:
        self.invalidated = True


class _StatusLockEngine:
    def __init__(self, connection: _StatusLockConnection) -> None:
        self.connection = connection
        self.connect_calls = 0

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[_StatusLockConnection]:
        self.connect_calls += 1
        yield self.connection


@pytest.mark.asyncio
async def test_status_target_locks_are_sorted_unique_and_released_in_reverse() -> None:
    connection = _StatusLockConnection()
    engine = _StatusLockEngine(connection)

    async with locked_status_targets(
        engine,  # type: ignore[arg-type]
        ad_ids=["302", "101", "302", " 203 "],
    ) as locks:
        assert locks.requested_ad_ids == ("101", "203", "302")
        assert locks.busy_ad_id is None
        assert connection.try_calls == ["101", "203", "302"]
        assert connection.unlock_calls == []

    assert connection.unlock_calls == ["302", "203", "101"]
    assert connection.commits == 2
    assert connection.invalidated is False


@pytest.mark.asyncio
async def test_status_target_lock_contention_stops_without_waiting_and_releases_prefix() -> None:
    connection = _StatusLockConnection(busy_ad_id="203")
    engine = _StatusLockEngine(connection)

    async with locked_status_targets(
        engine,  # type: ignore[arg-type]
        ad_ids=["302", "203", "101"],
    ) as locks:
        assert locks.requested_ad_ids == ("101", "203", "302")
        assert locks.busy_ad_id == "203"
        assert connection.try_calls == ["101", "203"]

    assert connection.unlock_calls == ["101"]


@pytest.mark.asyncio
async def test_status_target_locks_reject_an_empty_target_set_before_connect() -> None:
    connection = _StatusLockConnection()
    engine = _StatusLockEngine(connection)

    with pytest.raises(ValueError, match="at least one ad id"):
        async with locked_status_targets(
            engine,  # type: ignore[arg-type]
            ad_ids=["", "  "],
        ):
            raise AssertionError("empty target set must not enter the context")

    assert engine.connect_calls == 0

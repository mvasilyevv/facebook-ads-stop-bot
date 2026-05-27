# -*- coding: utf-8 -*-
"""Pure unit на дедуп-логику ingest_postback (без живого Postgres).

Через monkeypatch SQLAlchemy AsyncEngine моделируем сценарии:
- pre-INSERT SELECT нашёл строку → IngestResult(is_duplicate=True)
- pre-INSERT SELECT пуст + INSERT RETURNING row → IngestResult(inserted=True)
- pre-INSERT SELECT пуст + INSERT RETURNING None (race на UNIQUE) → is_duplicate=True
- fb_ad_id из payload резолвится через LOOKUP fb_ads.fb_ad_id
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from core.adset_pro.ingest import ingest_postback
from core.adset_pro.schemas import PostbackEvent


class _FakeRow:
    """Минимальная имитация Row.first() — кортеж по индексу."""

    def __init__(self, values: tuple[Any, ...]):
        self._values = values

    def __getitem__(self, idx: int) -> Any:
        return self._values[idx]


class _FakeResult:
    """Имитация sqlalchemy CursorResult: .first() / .scalar()."""

    def __init__(self, row: _FakeRow | None = None, scalar_value: Any = None):
        self._row = row
        self._scalar = scalar_value

    def first(self) -> _FakeRow | None:
        return self._row

    def scalar(self) -> Any:
        return self._scalar

    def scalar_one(self) -> Any:
        if self._scalar is None:
            raise ValueError("scalar_one(): no value")
        return self._scalar


class _FakeConn:
    """Имитация AsyncConnection.execute — берёт ответ из списка-сценария."""

    def __init__(self, plan: list[_FakeResult]):
        self._plan = plan
        self._step = 0
        self.executed: list[tuple[str, dict]] = []

    async def execute(self, stmt, params: dict | None = None) -> _FakeResult:
        sql_text = str(stmt)
        self.executed.append((sql_text, params or {}))
        if self._step >= len(self._plan):
            return _FakeResult(row=None)
        result = self._plan[self._step]
        self._step += 1
        return result


class _FakeEngine:
    """Имитация AsyncEngine: connect() и begin() возвращают один и тот же _FakeConn."""

    def __init__(self, plan: list[_FakeResult]):
        self.conn = _FakeConn(plan)

    @asynccontextmanager
    async def connect(self):
        yield self.conn

    @asynccontextmanager
    async def begin(self):
        yield self.conn


def _event(**overrides) -> PostbackEvent:
    """Сгенерировать минимальный PostbackEvent."""
    base = {
        "click_id": "click-1",
        "fb_ad_id": None,
        "event_type": "ftd",
        "revenue": Decimal("10"),
        "currency": "USD",
        "received_at": datetime.now(UTC),
        "raw": {},
    }
    base.update(overrides)
    return PostbackEvent(**base)


# Сценарий: pre-INSERT SELECT уже нашёл postback → возвращаем is_duplicate=True без INSERT'а.
@pytest.mark.asyncio
async def test_ingest_marks_duplicate_when_select_finds_row() -> None:
    plan = [
        _FakeResult(row=_FakeRow((42,))),  # pre-INSERT SELECT нашёл запись
    ]
    engine = _FakeEngine(plan)
    event = _event(click_id="dup-1")

    result = await ingest_postback(engine, event)

    assert result.inserted is False
    assert result.is_duplicate is True
    assert result.event_id is None
    # Должен быть только SELECT, без INSERT.
    assert len(engine.conn.executed) == 1
    assert "SELECT id FROM adsetpro_postback_events" in engine.conn.executed[0][0]


# Сценарий: pre-INSERT SELECT пуст + INSERT RETURNING строку → inserted=True.
@pytest.mark.asyncio
async def test_ingest_inserts_when_no_prior_row() -> None:
    plan = [
        _FakeResult(row=None),  # pre-INSERT SELECT — пусто
        _FakeResult(row=_FakeRow((1234,))),  # INSERT RETURNING id
    ]
    engine = _FakeEngine(plan)
    event = _event(click_id="new-1")

    result = await ingest_postback(engine, event)

    assert result.inserted is True
    assert result.is_duplicate is False
    assert result.event_id == 1234
    # Должно быть 2 операции: SELECT (dedup) + INSERT.
    assert len(engine.conn.executed) == 2
    assert "INSERT INTO adsetpro_postback_events" in engine.conn.executed[1][0]


# Сценарий: SELECT пуст + INSERT RETURNING None (race на UNIQUE) → is_duplicate=True.
@pytest.mark.asyncio
async def test_ingest_handles_race_on_unique_constraint() -> None:
    plan = [
        _FakeResult(row=None),  # pre-INSERT SELECT — пусто
        _FakeResult(row=None),  # INSERT RETURNING — None из-за ON CONFLICT DO NOTHING
    ]
    engine = _FakeEngine(plan)
    event = _event(click_id="race-1")

    result = await ingest_postback(engine, event)

    assert result.inserted is False
    assert result.is_duplicate is True
    assert result.event_id is None


# Сценарий: fb_ad_id из event → LOOKUP в fb_ads резолвит UUID → fb_ad_fk попадает в результат.
@pytest.mark.asyncio
async def test_ingest_resolves_fb_ad_fk_from_lookup() -> None:
    ad_uuid = uuid.uuid4()
    plan = [
        _FakeResult(row=_FakeRow((ad_uuid,))),  # _resolve_fb_ad_fk: SELECT fb_ads
        _FakeResult(row=None),  # pre-INSERT SELECT — пусто
        _FakeResult(row=_FakeRow((99,))),  # INSERT RETURNING id
    ]
    engine = _FakeEngine(plan)
    event = _event(click_id="lookup-1", fb_ad_id="231234567890")

    result = await ingest_postback(engine, event)

    assert result.inserted is True
    assert result.fb_ad_fk == ad_uuid
    # Первый запрос — это LOOKUP к fb_ads, до dedup-проверки.
    assert "SELECT id FROM fb_ads" in engine.conn.executed[0][0]


# Сценарий: fb_ad_id не передан → LOOKUP вообще не делается, fb_ad_fk остаётся None.
@pytest.mark.asyncio
async def test_ingest_skips_lookup_when_no_fb_ad_id() -> None:
    plan = [
        _FakeResult(row=None),  # pre-INSERT SELECT — пусто
        _FakeResult(row=_FakeRow((7,))),  # INSERT RETURNING id
    ]
    engine = _FakeEngine(plan)
    event = _event(click_id="nofb-1", fb_ad_id=None)

    result = await ingest_postback(engine, event)

    assert result.inserted is True
    assert result.fb_ad_fk is None
    # SELECT в fb_ads не должен делаться.
    assert not any("FROM fb_ads" in sql for sql, _ in engine.conn.executed)

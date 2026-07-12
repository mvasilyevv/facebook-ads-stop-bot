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


# Advisory-lock — первый execute внутри begin(); _FakeResult(row=None) достаточно, он
# ничего не возвращает. Держим общий хелпер, чтобы план тестов читался ближе к реальному SQL.
def _lock_step() -> _FakeResult:
    """Шаг плана под SELECT pg_advisory_xact_lock(...) — результат не важен."""
    return _FakeResult(row=None)


# Сценарий: pre-INSERT SELECT уже нашёл postback → возвращаем is_duplicate=True без INSERT'а.
@pytest.mark.asyncio
async def test_ingest_marks_duplicate_when_select_finds_row() -> None:
    plan = [
        _lock_step(),  # pg_advisory_xact_lock
        _FakeResult(row=_FakeRow((42,))),  # pre-INSERT SELECT нашёл запись
    ]
    engine = _FakeEngine(plan)
    event = _event(click_id="dup-1")

    result = await ingest_postback(engine, event)

    assert result.inserted is False
    assert result.is_duplicate is True
    assert result.event_id is None
    # Должны быть только advisory-lock и SELECT, без INSERT.
    assert len(engine.conn.executed) == 2
    assert "pg_advisory_xact_lock" in engine.conn.executed[0][0]
    assert "SELECT id FROM adsetpro_postback_events" in engine.conn.executed[1][0]


# Сценарий: pre-INSERT SELECT пуст + INSERT RETURNING строку → inserted=True.
@pytest.mark.asyncio
async def test_ingest_inserts_when_no_prior_row() -> None:
    plan = [
        _lock_step(),  # pg_advisory_xact_lock
        _FakeResult(row=None),  # pre-INSERT SELECT — пусто
        _FakeResult(row=_FakeRow((1234,))),  # INSERT RETURNING id
    ]
    engine = _FakeEngine(plan)
    event = _event(click_id="new-1")

    result = await ingest_postback(engine, event)

    assert result.inserted is True
    assert result.is_duplicate is False
    assert result.event_id == 1234
    # Должно быть 3 операции: advisory-lock + SELECT (dedup) + INSERT.
    assert len(engine.conn.executed) == 3
    assert "INSERT INTO adsetpro_postback_events" in engine.conn.executed[2][0]


# Сценарий: SELECT пуст + INSERT RETURNING None (race на UNIQUE) → is_duplicate=True.
@pytest.mark.asyncio
async def test_ingest_handles_race_on_unique_constraint() -> None:
    plan = [
        _lock_step(),  # pg_advisory_xact_lock
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
        _lock_step(),  # pg_advisory_xact_lock
        _FakeResult(row=None),  # pre-INSERT SELECT — пусто
        _FakeResult(row=_FakeRow((99,))),  # INSERT RETURNING id
    ]
    engine = _FakeEngine(plan)
    event = _event(click_id="lookup-1", fb_ad_id="231234567890")

    result = await ingest_postback(engine, event)

    assert result.inserted is True
    assert result.fb_ad_fk == ad_uuid
    # Первый запрос — это LOOKUP к fb_ads, до advisory-lock и dedup-проверки.
    assert "SELECT id FROM fb_ads" in engine.conn.executed[0][0]


# Сценарий: fb_ad_id не передан → LOOKUP вообще не делается, fb_ad_fk остаётся None.
@pytest.mark.asyncio
async def test_ingest_skips_lookup_when_no_fb_ad_id() -> None:
    plan = [
        _lock_step(),  # pg_advisory_xact_lock
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


# Сценарий (H-5): advisory-lock берётся ПЕРВЫМ в транзакции (до pre-SELECT), с ключом
# click_id:event_type — сериализация конкурентного дедупа одного события.
@pytest.mark.asyncio
async def test_ingest_takes_advisory_lock_with_expected_key_before_select() -> None:
    plan = [
        _lock_step(),  # pg_advisory_xact_lock
        _FakeResult(row=None),  # pre-INSERT SELECT — пусто
        _FakeResult(row=_FakeRow((5,))),  # INSERT RETURNING id
    ]
    engine = _FakeEngine(plan)
    event = _event(click_id="lock-key-1", event_type="ftd", fb_ad_id=None)

    await ingest_postback(engine, event)

    executed = engine.conn.executed
    # Первый SQL внутри транзакции — advisory-lock, СТРОГО до pre-SELECT дедупа.
    assert "pg_advisory_xact_lock" in executed[0][0]
    assert "hashtext" in executed[0][0]
    # Ключ лока == дедуп-ключ pre-SELECT'а: click_id:event_type.
    assert executed[0][1]["lock_key"] == "lock-key-1:ftd"
    # Именно после лока идёт pre-INSERT SELECT.
    assert "SELECT id FROM adsetpro_postback_events" in executed[1][0]
    # Индекс advisory-lock строго меньше индекса SELECT.
    lock_idx = next(i for i, (sql, _) in enumerate(executed) if "pg_advisory_xact_lock" in sql)
    select_idx = next(
        i for i, (sql, _) in enumerate(executed) if "SELECT id FROM adsetpro_postback_events" in sql
    )
    assert lock_idx < select_idx


# Сценарий (H-5 регресс): поведение дедупа для последовательных дублей не изменилось —
# добавление advisory-lock не сломало старую логику pre-SELECT → is_duplicate.
@pytest.mark.asyncio
async def test_advisory_lock_does_not_change_sequential_dedup_behavior() -> None:
    # Первый ingest: лок + пустой pre-SELECT + INSERT RETURNING id → inserted=True.
    first_plan = [
        _lock_step(),
        _FakeResult(row=None),
        _FakeResult(row=_FakeRow((100,))),
    ]
    first_engine = _FakeEngine(first_plan)
    event = _event(click_id="seq-dup-1", event_type="ftd")
    first = await ingest_postback(first_engine, event)
    assert first.inserted is True
    assert first.is_duplicate is False

    # Повторный ingest того же события: лок + pre-SELECT нашёл строку → is_duplicate=True без INSERT.
    second_plan = [
        _lock_step(),
        _FakeResult(row=_FakeRow((100,))),
    ]
    second_engine = _FakeEngine(second_plan)
    second = await ingest_postback(second_engine, event)
    assert second.inserted is False
    assert second.is_duplicate is True
    assert second.event_id is None
    # INSERT второй раз не выполнялся.
    assert not any(
        "INSERT INTO adsetpro_postback_events" in sql for sql, _ in second_engine.conn.executed
    )


# ─── Аудит 2026-07-12 (H-4): повторяемые события и txn-дедуп ─────────────────


# redep/baddep повторяемы по одному click_id — окно дедупа только анти-ретрай (минуты);
# одноразовые (ftd и прочие) — прежние 24 часа. Регистр не влияет.
def test_dedup_window_repeatable_vs_one_shot() -> None:
    from datetime import timedelta

    from core.adset_pro.ingest import (
        _DEDUP_WINDOW,
        _DEDUP_WINDOW_REPEATABLE,
        dedup_window_for,
    )

    assert dedup_window_for("ftd") == _DEDUP_WINDOW == timedelta(hours=24)
    assert dedup_window_for("redep") == _DEDUP_WINDOW_REPEATABLE == timedelta(minutes=10)
    assert dedup_window_for("REDEP") == _DEDUP_WINDOW_REPEATABLE
    assert dedup_window_for("baddep") == _DEDUP_WINDOW_REPEATABLE
    assert dedup_window_for("") == _DEDUP_WINDOW
    assert dedup_window_for("reg") == _DEDUP_WINDOW


# txn-id извлекается по известным ключам (первый непустой), пустые/отсутствующие → None.
def test_txn_id_from_raw() -> None:
    from core.adset_pro.ingest import _txn_id_from_raw

    assert _txn_id_from_raw({"transaction_id": "t-1"}) == "t-1"
    assert _txn_id_from_raw({"txn_id": 42}) == "42"
    assert _txn_id_from_raw({"transaction_id": "", "conversion_id": "c-9"}) == "c-9"
    assert _txn_id_from_raw({"unrelated": "x"}) is None
    assert _txn_id_from_raw({}) is None
    assert _txn_id_from_raw(None) is None


# Анти-дрейф контракта: каждый ключ из _TXN_ID_RAW_KEYS обязан присутствовать
# в COALESCE dedup-SQL — иначе txn-дедуп молча перестанет видеть этот ключ.
@pytest.mark.asyncio
async def test_dedup_sql_covers_all_txn_keys() -> None:
    from core.adset_pro.ingest import _TXN_ID_RAW_KEYS

    plan = [
        _lock_step(),
        _FakeResult(row=None),  # pre-INSERT SELECT — пусто
        _FakeResult(row=_FakeRow((1,))),  # INSERT RETURNING id
    ]
    engine = _FakeEngine(plan)
    await ingest_postback(engine, _event(click_id="sqlkeys-1"))

    select_sql = engine.conn.executed[1][0]
    for key in _TXN_ID_RAW_KEYS:
        assert f"raw_json->>'{key}'" in select_sql, f"ключ {key} выпал из dedup-SQL"


# redep с txn-id: в dedup-SELECT уходят txn_id, анти-ретрай type_since (10 мин)
# и широкая 24ч граница since (partition pruning).
@pytest.mark.asyncio
async def test_redep_dedup_params_short_window_and_txn() -> None:
    from datetime import timedelta

    plan = [
        _lock_step(),
        _FakeResult(row=None),
        _FakeResult(row=_FakeRow((1,))),
    ]
    engine = _FakeEngine(plan)
    now = datetime.now(UTC)
    event = _event(
        click_id="rdp-1",
        event_type="redep",
        received_at=now,
        raw={"transaction_id": "tx-7"},
    )
    await ingest_postback(engine, event)

    params = engine.conn.executed[1][1]
    assert params["txn_id"] == "tx-7"
    assert params["type_since"] == now - timedelta(minutes=10)
    assert params["since"] == now - timedelta(hours=24)


# ftd без txn-id: type_since совпадает с 24ч границей (поведение не изменилось).
@pytest.mark.asyncio
async def test_ftd_dedup_params_unchanged() -> None:
    from datetime import timedelta

    plan = [
        _lock_step(),
        _FakeResult(row=None),
        _FakeResult(row=_FakeRow((1,))),
    ]
    engine = _FakeEngine(plan)
    now = datetime.now(UTC)
    await ingest_postback(engine, _event(click_id="ftd-1", event_type="ftd", received_at=now))

    params = engine.conn.executed[1][1]
    assert params["txn_id"] is None
    assert params["type_since"] == now - timedelta(hours=24)
    assert params["since"] == now - timedelta(hours=24)


# M-4 (аудит 2026-07-12): event_type нормализуется в lowercase в ingest'е.
# 'FTD'/'Ftd' от AdSet.pro раньше молча не матчился в evaluator/aggregate
# (DEPOSIT_EVENT_TYPES lowercase, SQL регистро-чувствителен) → недосчёт депозитов.
@pytest.mark.asyncio
async def test_ingest_normalizes_event_type_case() -> None:
    plan = [
        _lock_step(),
        _FakeResult(row=None),  # pre-INSERT SELECT — пусто
        _FakeResult(row=_FakeRow((1,))),  # INSERT RETURNING id
    ]
    engine = _FakeEngine(plan)
    await ingest_postback(engine, _event(click_id="case-1", event_type="FTD"))

    # И в pre-SELECT, и в INSERT event_type уже lowercase.
    select_params = engine.conn.executed[1][1]
    insert_params = engine.conn.executed[2][1]
    assert select_params["event_type"] == "ftd"
    assert insert_params["event_type"] == "ftd"


# 'ReDep' с пробелами → 'redep' + повторяемое окно (10 мин), а не 24ч.
@pytest.mark.asyncio
async def test_ingest_normalizes_and_uses_repeatable_window() -> None:
    from datetime import timedelta

    plan = [_lock_step(), _FakeResult(row=None), _FakeResult(row=_FakeRow((1,)))]
    engine = _FakeEngine(plan)
    now = datetime.now(UTC)
    await ingest_postback(engine, _event(click_id="case-2", event_type="  ReDep ", received_at=now))

    params = engine.conn.executed[1][1]
    assert params["event_type"] == "redep"
    assert params["type_since"] == now - timedelta(minutes=10)

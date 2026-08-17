"""Unit contracts for canonical AdSet.pro ingest and atomic durable enqueue."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from core.adset_pro.ingest import (
    canonical_event_type,
    ingest_postback,
    provider_event_id_from_raw,
)
from core.adset_pro.schemas import PostbackEvent


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None):
        self.rows = rows or []

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None

    def one(self):
        assert len(self.rows) == 1
        return self.rows[0]


class _Conn:
    def __init__(self, results: list[_Result]):
        self.results = list(results)
        self.executed: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement, params=None):
        self.executed.append((str(statement), params or {}))
        return self.results.pop(0)


class _Engine:
    def __init__(self, results: list[_Result]):
        self.conn = _Conn(results)
        self.begin_count = 0

    @asynccontextmanager
    async def begin(self):
        self.begin_count += 1
        yield self.conn


def _event(**overrides: Any) -> PostbackEvent:
    values = {
        "click_id": "click-1",
        "fb_ad_id": None,
        "event_type": "ftd",
        "revenue": Decimal("10"),
        "currency": "USD",
        "received_at": datetime.now(UTC),
        "raw": {},
    }
    values.update(overrides)
    return PostbackEvent(**values)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("reg", "registration"),
        ("Registration", "registration"),
        ("hold", "registration"),
        ("CPA_HOLD", "registration"),
        ("FTD", "ftd"),
        ("accept", "ftd"),
        ("CPA_ACCEPT", "ftd"),
        ("redep", "redeposit"),
        ("CPA_REDEP", "redeposit"),
        ("baddep", None),
        ("decline", None),
        ("unknown", None),
    ],
)
def test_canonical_event_type_allows_only_positive_domain_events(raw: str, expected: str | None):
    assert canonical_event_type(raw) == expected


def test_provider_event_id_uses_first_stable_alias() -> None:
    assert provider_event_id_from_raw({"transaction_id": "tx-1"}) == "tx-1"
    assert provider_event_id_from_raw({"transaction_id": "", "conversion_id": 42}) == "42"
    assert provider_event_id_from_raw({"ext_click_id": "click-not-transaction"}) is None
    assert provider_event_id_from_raw({"click_id": "not-a-transaction"}) is None


@pytest.mark.asyncio
async def test_ingest_inserts_event_and_processing_task_in_one_transaction() -> None:
    received_at = datetime.now(UTC)
    engine = _Engine(
        [
            _Result(),  # advisory lock
            _Result(),  # exact one-shot dedupe lookup
            _Result([(11, received_at)]),  # event insert
            _Result([(received_at,)]),  # PostgreSQL scheduler clock
            _Result([(91,)]),  # durable task insert
            _Result(),  # transactional pg_notify wakeup hint
        ]
    )

    result = await ingest_postback(engine, _event(received_at=received_at))

    assert engine.begin_count == 1
    assert result.inserted is True
    assert result.event_id == 11
    assert result.task_id == 91
    sql = [statement for statement, _ in engine.conn.executed]
    assert "pg_advisory_xact_lock" in sql[0]
    assert "INSERT INTO adsetpro_postback_events" in sql[2]
    assert "clock_timestamp()" in sql[3]
    assert "INSERT INTO task_queue" in sql[4]
    assert "pg_notify" in sql[5]
    task_params = engine.conn.executed[4][1]
    assert task_params["tt"] == "tracker_event_process"
    assert task_params["lane"] == "background"
    assert task_params["deadline_at"] > received_at
    assert task_params["correlation_id"] is not None
    assert engine.conn.executed[5][1]["channel"] == "fb_task_queue"


@pytest.mark.asyncio
async def test_ingest_dedupes_one_shot_by_source_click_and_type() -> None:
    engine = _Engine(
        [
            _Result(),
            _Result([(11, None, "unmatched")]),
            _Result([(12,)]),
        ]
    )
    result = await ingest_postback(engine, _event(click_id="same"))
    assert result.inserted is False
    assert result.is_duplicate is True
    assert result.event_id == 12
    duplicate_sql = engine.conn.executed[2][0]
    assert "is_duplicate" in duplicate_sql
    assert "TRUE" in duplicate_sql
    assert not any("INSERT INTO task_queue" in statement for statement, _ in engine.conn.executed)


@pytest.mark.asyncio
async def test_redeposit_without_provider_id_is_rejected_before_db_write() -> None:
    engine = _Engine([])
    with pytest.raises(ValueError, match="requires provider_event_id"):
        await ingest_postback(engine, _event(event_type="redeposit"))
    assert engine.begin_count == 0


@pytest.mark.asyncio
async def test_provider_id_dedupe_is_independent_of_click_and_event_type() -> None:
    engine = _Engine(
        [
            _Result(),
            _Result([(12, None, "unmatched")]),
            _Result([(13,)]),
        ]
    )
    result = await ingest_postback(
        engine,
        _event(event_type="redeposit", provider_event_id="provider-42"),
    )
    assert result.is_duplicate is True
    dedupe_sql, params = engine.conn.executed[1]
    assert "source = :source" in dedupe_sql
    assert "provider_event_id," in dedupe_sql
    assert "raw_json->>'transaction_id'" in dedupe_sql
    assert ") = :provider_event_id" in dedupe_sql
    assert params["provider_event_id"] == "provider-42"


@pytest.mark.asyncio
async def test_one_shot_ignores_provider_delivery_id_for_dedupe_key() -> None:
    engine = _Engine(
        [
            _Result(),
            _Result([(21, None, "unmatched")]),
            _Result([(22,)]),
        ]
    )
    result = await ingest_postback(
        engine,
        _event(event_type="ftd", provider_event_id="delivery-retry-2"),
    )

    assert result.is_duplicate is True
    assert engine.conn.executed[0][1]["lock_key"] == "adsetpro:click:click-1:ftd"
    dedupe_sql, dedupe_params = engine.conn.executed[1]
    assert "click_id = :click_id" in dedupe_sql
    assert "event_type = :event_type" in dedupe_sql
    assert dedupe_params["click_id"] == "click-1"


@pytest.mark.asyncio
async def test_postback_outlives_a_deploy_window() -> None:
    """Постбек — это конверсия, а не запрос пользователя.

    Гейт claim'а очереди — `deadline_at > clock_timestamp()`, поэтому 120 секунд
    означали «переживи деплой или умри». 16.08 так умерли 7 конверсий одним
    пакетом, не дойдя до внешнего вызова. Длительность одного захода
    ограничивает лиз очереди (30 минут), а не этот срок.
    """
    received_at = datetime.now(UTC)
    engine = _Engine(
        [
            _Result(),  # advisory lock
            _Result(),  # exact one-shot dedupe lookup
            _Result([(11, received_at)]),  # event insert
            _Result([(received_at,)]),  # PostgreSQL scheduler clock
            _Result([(91,)]),  # durable task insert
            _Result(),  # transactional pg_notify wakeup hint
        ]
    )

    await ingest_postback(engine, _event(received_at=received_at))

    task_params = engine.conn.executed[4][1]
    assert task_params["tt"] == "tracker_event_process"
    assert task_params["deadline_at"] - received_at >= timedelta(hours=6)

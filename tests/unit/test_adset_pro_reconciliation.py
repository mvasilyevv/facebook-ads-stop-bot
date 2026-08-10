# -*- coding: utf-8 -*-
"""Focused tests for the AdSet.pro provider repair loop."""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

import core.adset_pro.reconciliation as reconciliation
from core.adset_pro.ingest import IngestResult
from core.adset_pro.reconciliation import ProviderReconciliationResult
from core.adset_pro.schemas import ConversionRow
from core.analytics import DEFAULT_ANALYTICS_WINDOW


class _FakeClient:
    def __init__(
        self,
        rows: list[ConversionRow] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.rows = rows or []
        self.error = error
        self.started = False
        self.closed = False
        self.requested: tuple[date, date] | None = None

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    async def list_conversions(self, *, since: date, until: date):
        self.requested = (since, until)
        if self.error is not None:
            raise self.error
        return list(self.rows)


def _row(
    *,
    click_id: str,
    event_type: str,
    raw: dict | None = None,
) -> ConversionRow:
    return ConversionRow(
        click_id=click_id,
        fb_ad_id="120000000001",
        event_type=event_type,
        revenue=Decimal("12.50"),
        currency="USD",
        occurred_at=datetime(2026, 7, 14, 10, 0, tzinfo=UTC),
        raw=raw or {},
    )


@pytest.mark.asyncio
async def test_reconcile_normalizes_aliases_and_repairs_only_missing(monkeypatch) -> None:
    rows = [
        _row(click_id="click-1", event_type="CPA_HOLD"),
        _row(click_id="click-2", event_type="accept"),
        _row(click_id="click-3", event_type="CPA_REDEP"),
        _row(click_id="click-4", event_type="rejected"),
    ]
    client = _FakeClient(rows)
    registration = ("registration", "click-1", "")
    ftd = ("ftd", "click-2", "")
    local_facts = AsyncMock(side_effect=[{ftd}, {registration, ftd}])
    audit = AsyncMock()
    ingested_events = []

    async def _ingest(_engine, event, *, record_duplicate):
        assert record_duplicate is False
        ingested_events.append(event)
        return IngestResult(
            inserted=event.click_id == "click-1",
            is_duplicate=event.click_id == "click-2",
            event_id=1,
            fb_ad_fk=None,
        )

    monkeypatch.setattr(reconciliation, "_local_fact_keys", local_facts)
    monkeypatch.setattr(reconciliation, "_write_audit", audit)
    monkeypatch.setattr(reconciliation, "ingest_postback", _ingest)

    result = await reconciliation.reconcile_provider_events(
        object(),
        now=datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
        client=client,
    )

    assert result.status == "ok"
    assert result.provider_rows == 4
    assert result.provider_facts == 2
    assert result.missing == 1
    assert result.accepted == 1
    assert result.duplicates == 1
    assert result.skipped == 2
    assert result.drift_before == 1
    assert result.drift_after == 0
    assert [event.event_type for event in ingested_events] == ["registration", "ftd"]
    assert reconciliation.DEFAULT_PROVIDER_LOOKBACK == DEFAULT_ANALYTICS_WINDOW
    assert client.requested == (date(2026, 7, 7), date(2026, 7, 14))
    audit.assert_awaited_once()


def test_redeposit_requires_stable_provider_transaction_id() -> None:
    unstable = _row(click_id="click-1", event_type="redep")
    stable = _row(
        click_id="click-1",
        event_type="redep",
        raw={"transactionId": "tx-123"},
    )

    assert reconciliation._fact_key(unstable) is None
    assert reconciliation._fact_key(stable) == ("redeposit", "click-1", "tx-123")


@pytest.mark.asyncio
async def test_reconcile_uses_db_first_factory_and_closes_owned_client(monkeypatch) -> None:
    client = _FakeClient()
    resolve_key = AsyncMock(return_value="mcp_db_key")
    create_client = AsyncMock(return_value=client)
    local_facts = AsyncMock(side_effect=[set(), set()])
    audit = AsyncMock()
    monkeypatch.setattr(reconciliation, "resolve_adsetpro_api_key", resolve_key)
    monkeypatch.setattr(reconciliation, "create_adsetpro_client", create_client)
    monkeypatch.setattr(reconciliation, "_local_fact_keys", local_facts)
    monkeypatch.setattr(reconciliation, "_write_audit", audit)

    result = await reconciliation.reconcile_provider_events(
        object(),
        now=datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
    )

    assert result.status == "ok"
    resolve_key.assert_awaited_once()
    create_client.assert_awaited_once()
    assert create_client.await_args.kwargs == {"api_key": "mcp_db_key"}
    assert client.started is True
    assert client.closed is True


@pytest.mark.asyncio
async def test_provider_outage_is_redacted_audited_and_never_raised(
    monkeypatch,
    caplog,
) -> None:
    secret = "super-secret-query-token"
    client = _FakeClient(error=RuntimeError(f"GET ?token={secret}"))
    audit = AsyncMock()
    monkeypatch.setattr(reconciliation, "_write_audit", audit)

    with caplog.at_level(logging.WARNING):
        result = await reconciliation.reconcile_provider_events(
            object(),
            now=datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
            client=client,
        )

    assert result.status == "error"
    assert result.error == "RuntimeError"
    assert secret not in caplog.text
    assert secret not in json.dumps(audit.await_args.args[1], default=str)
    audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_one_bad_row_yields_partial_result_and_continues(monkeypatch) -> None:
    rows = [
        _row(click_id="bad", event_type="registration"),
        _row(click_id="good", event_type="ftd"),
    ]
    provider_facts = {("registration", "bad", ""), ("ftd", "good", "")}
    local_facts = AsyncMock(side_effect=[set(), {("ftd", "good", "")}])
    audit = AsyncMock()
    calls = 0

    async def _ingest(_engine, event, *, record_duplicate):
        nonlocal calls
        calls += 1
        if event.click_id == "bad":
            raise ValueError("token=must-not-be-recorded")
        return IngestResult(
            inserted=True,
            is_duplicate=False,
            event_id=2,
            fb_ad_fk=None,
        )

    monkeypatch.setattr(reconciliation, "_local_fact_keys", local_facts)
    monkeypatch.setattr(reconciliation, "_write_audit", audit)
    monkeypatch.setattr(reconciliation, "ingest_postback", _ingest)

    result = await reconciliation.reconcile_provider_events(
        object(),
        now=datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
        client=_FakeClient(rows),
    )

    assert calls == 2
    assert result.status == "partial"
    assert result.provider_facts == len(provider_facts)
    assert result.missing == 2
    assert result.accepted == 1
    assert result.drift_after == 1
    assert result.error == "1 row(s): ValueError"


@pytest.mark.parametrize(
    ("raw_revenue", "issue"),
    [
        (None, "revenue_missing"),
        ("not-a-number", "revenue_invalid"),
        ("NaN", "revenue_invalid"),
    ],
)
@pytest.mark.asyncio
async def test_provider_unknown_revenue_is_ingested_as_null_and_marks_partial(
    monkeypatch,
    raw_revenue,
    issue,
) -> None:
    raw = {
        "click_id": "quality-row",
        "event_type": "ftd",
        "ext_sub8": "120000000001",
        "currency": "USD",
    }
    if raw_revenue is not None:
        raw["revenue"] = raw_revenue
    row = ConversionRow.from_api_row(raw)
    local_facts = AsyncMock(side_effect=[set(), {("ftd", "quality-row", "")}])
    audit = AsyncMock()
    ingested_events = []

    async def _ingest(_engine, event, *, record_duplicate):
        assert record_duplicate is False
        ingested_events.append(event)
        return IngestResult(
            inserted=True,
            is_duplicate=False,
            event_id=3,
            fb_ad_fk=None,
        )

    monkeypatch.setattr(reconciliation, "_local_fact_keys", local_facts)
    monkeypatch.setattr(reconciliation, "_write_audit", audit)
    monkeypatch.setattr(reconciliation, "ingest_postback", _ingest)

    result = await reconciliation.reconcile_provider_events(
        object(),
        now=datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
        client=_FakeClient([row]),
    )

    assert row.issues == (issue,)
    assert [event.revenue for event in ingested_events] == [None]
    assert result.accepted == 1
    assert result.status == "partial"
    assert result.error == f"1 incomplete row(s): {issue}"
    audit.assert_awaited_once()


class _CapturingConnection:
    def __init__(self) -> None:
        self.params = None

    async def execute(self, _statement, params):
        self.params = params


class _AsyncContext:
    def __init__(self, value) -> None:
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        return None


class _CapturingEngine:
    def __init__(self) -> None:
        self.connection = _CapturingConnection()

    def begin(self):
        return _AsyncContext(self.connection)


@pytest.mark.asyncio
async def test_audit_payload_contains_operator_counts_and_drift() -> None:
    engine = _CapturingEngine()
    now = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
    result = ProviderReconciliationResult(
        status="ok",
        checked_at=now,
        window_start=now,
        window_end=now,
        provider_rows=8,
        accepted=2,
        missing=2,
        duplicates=4,
        skipped=2,
        drift_before=2,
        drift_after=0,
    )

    await reconciliation._write_audit(engine, result)

    assert engine.connection.params["key"] == "tracker_provider_reconciliation"
    payload = json.loads(engine.connection.params["value"])
    assert payload["provider_rows"] == 8
    assert payload["accepted"] == 2
    assert payload["missing"] == 2
    assert payload["duplicates"] == 4
    assert payload["skipped"] == 2
    assert payload["drift_before"] == 2
    assert payload["drift_after"] == 0

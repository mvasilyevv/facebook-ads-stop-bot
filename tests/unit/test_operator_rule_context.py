from __future__ import annotations

import importlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.api.routers.v1.operator import (
    _approaching_stop_section,
    _hide_unconfirmed_rule_money,
    _redact_approaching_stop_row,
)
from apps.api.routers.v1.schemas.operator import OperatorSeverity
from core.observer.writers import upsert_catalog_hierarchy
from core.operator.queries import _operator_rule_context


class _RowResult:
    def __init__(self, value: uuid.UUID) -> None:
        self._value = value

    def first(self) -> tuple[uuid.UUID]:
        return (self._value,)


class _CatalogConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: object, params: dict[str, Any]) -> _RowResult:
        self.calls.append((str(statement), params))
        return _RowResult(uuid.uuid4())


class _CatalogTransaction:
    def __init__(self, connection: _CatalogConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _CatalogConnection:
        return self.connection

    async def __aexit__(self, *args: object) -> None:
        return None


class _CatalogEngine:
    def __init__(self) -> None:
        self.connection = _CatalogConnection()

    def begin(self) -> _CatalogTransaction:
        return _CatalogTransaction(self.connection)


@pytest.mark.asyncio
async def test_catalog_upsert_writes_rule_context_on_insert_and_conflict_update() -> None:
    fake_engine = _CatalogEngine()

    await upsert_catalog_hierarchy(
        cast(AsyncEngine, cast(Any, fake_engine)),
        fb_ad_id="120200000000001",
        ad_name="CR2 creative",
        fb_adset_id="120200000000002",
        adset_name="CR2 broad",
        fb_campaign_id="120200000000003",
        campaign_name="MV | CR2",
        offer_id=uuid.uuid4(),
        delivery_status="ACTIVE",
        ad_account_id="123",
        nearest_rule_code="cpr_stop",
        nearest_rule_value=Decimal("0.41"),
        nearest_rule_threshold=Decimal("0.48"),
        nearest_rule_stage="warning",
        matched_offer_code="CR2",
    )

    ad_sql, params = fake_engine.connection.calls[-1]
    assert "nearest_rule_code, nearest_rule_value" in ad_sql
    assert "nearest_rule_threshold, nearest_rule_stage" in ad_sql
    assert "matched_offer_code" in ad_sql
    assert "nearest_rule_code = EXCLUDED.nearest_rule_code" in ad_sql
    assert "nearest_rule_value = EXCLUDED.nearest_rule_value" in ad_sql
    assert "nearest_rule_threshold = EXCLUDED.nearest_rule_threshold" in ad_sql
    assert "nearest_rule_stage = EXCLUDED.nearest_rule_stage" in ad_sql
    assert "matched_offer_code = EXCLUDED.matched_offer_code" in ad_sql
    assert params["nr_code"] == "cpr_stop"
    assert params["nr_value"] == Decimal("0.41")
    assert params["nr_threshold"] == Decimal("0.48")
    assert params["nr_stage"] == "warning"
    assert params["offer_code"] == "CR2"


def test_rule_context_has_null_offer_for_unmatched_ad() -> None:
    context = _operator_rule_context(
        stored_offer_code=None,
        rule_code=None,
        value=None,
        threshold=None,
        stage=None,
    )

    assert context == {
        "offer_code": None,
        "rule_code": None,
        "rule_title": None,
        "value": None,
        "threshold": None,
        "percent_to_stop": None,
        "stage": "none",
    }


def test_rule_context_keeps_matched_offer_when_metric_is_unknown() -> None:
    context = _operator_rule_context(
        stored_offer_code="CR2",
        rule_code=None,
        value=None,
        threshold=None,
        stage=None,
    )

    assert context == {
        "offer_code": "CR2",
        "rule_code": None,
        "rule_title": None,
        "value": None,
        "threshold": None,
        "percent_to_stop": None,
        "stage": "none",
    }


def test_rule_context_percent_can_exceed_one_hundred_after_stop() -> None:
    context = _operator_rule_context(
        stored_offer_code="CR2",
        rule_code="cpr_stop",
        value=Decimal("0.60"),
        threshold=Decimal("0.48"),
        stage="stop",
    )

    assert context["offer_code"] == "CR2"
    assert context["rule_title"] == "Дорогая рега"
    assert context["value"] == "0.60"
    assert context["threshold"] == "0.48"
    assert context["percent_to_stop"] == "125.00"
    assert context["stage"] == "stop"


def _approaching_row(
    *,
    row_id: str,
    percent_to_stop: str,
    data_state: str = "ready",
) -> dict[str, Any]:
    return {
        "id": row_id,
        "fb_ad_id": f"fb-{row_id}",
        "name": f"Ad {row_id}",
        "campaign_id": f"campaign-{row_id}",
        "campaign_name": "MV | CR2",
        "adset_id": f"adset-{row_id}",
        "adset_name": "CR2 broad",
        "account_id": "123",
        "delivery_status": "ACTIVE",
        "data_state": data_state,
        "severity": "warning" if data_state == "ready" else "unknown",
        "as_of": datetime(2026, 8, 14, 10, tzinfo=UTC),
        "metrics": {
            "spend": "1.00",
            "impressions": 100,
            "clicks": 10,
            "registrations": 1,
            "ftd": 0,
            "confirmed_deposits": 0,
            "cpc": "0.10",
            "cost_per_registration": "1.00",
            "frequency": "1.20",
            "cost_per_ftd": None,
        },
        "rule_context": {
            "offer_code": "CR2",
            "rule_code": "cpr_stop",
            "rule_title": "Дорогая рега",
            "value": "0.41",
            "threshold": "0.48",
            "percent_to_stop": percent_to_stop,
            "stage": "warning",
        },
        "active_action": None,
    }


def test_approaching_stop_section_ranks_descending_and_preserves_data_state() -> None:
    now = datetime(2026, 8, 14, 10, 0, 30, tzinfo=UTC)
    section = _approaching_stop_section(
        rows=[
            _approaching_row(row_id="low", percent_to_stop="72.00"),
            _approaching_row(
                row_id="stale",
                percent_to_stop="91.50",
                data_state="stale",
            ),
            _approaching_row(row_id="middle", percent_to_stop="84.00"),
        ],
        now=now,
    )

    assert section.state == "partial"
    assert section.data is not None
    assert [item.id for item in section.data.items] == ["stale", "middle", "low"]
    assert section.data.items[0].data_state == "stale"


def test_approaching_stop_section_keeps_unconfirmed_delivery_status_as_unknown() -> None:
    """Issue 352: an unconfirmed delivery status is "we don't know", not

    "we know it's inactive". Dropping the row would be a dangerous-direction
    false negative — a still-delivering ad silently missing from the early
    warning feed. It must stay, and never read as a clean "ok" row.
    """
    row = _approaching_row(row_id="unconfirmed", percent_to_stop="80.00")
    row["delivery_status"] = None
    row["severity"] = "ok"

    section = _approaching_stop_section(
        rows=[row], now=datetime(2026, 8, 14, 10, 0, 30, tzinfo=UTC)
    )

    assert section.data is not None
    assert [item.id for item in section.data.items] == ["unconfirmed"]
    assert section.data.items[0].severity == OperatorSeverity.UNKNOWN


def test_approaching_stop_section_drops_only_confirmed_inactive_status() -> None:
    """A status Meta actually confirmed as inactive (PAUSED) legitimately

    disappears — this is the "we know" case #352 does not touch.
    """
    row = _approaching_row(row_id="paused", percent_to_stop="80.00")
    row["delivery_status"] = "PAUSED"

    section = _approaching_stop_section(rows=[row], now=datetime(2026, 8, 14, 10, tzinfo=UTC))

    assert section.data is not None
    assert section.data.items == []


def test_hide_unconfirmed_rule_money_hides_percent_alongside_value_and_threshold() -> None:
    """Issue 353: a percent without numerator/denominator doesn't tell the

    operator anything — it must disappear together with value/threshold, not
    survive alone.
    """
    row = _approaching_row(row_id="ad", percent_to_stop="84.00")
    rows = [row]

    _hide_unconfirmed_rule_money(rows)

    context = rows[0]["rule_context"]
    assert context["value"] is None
    assert context["threshold"] is None
    assert context["percent_to_stop"] is None
    metrics = rows[0]["metrics"]
    assert metrics["spend"] is None
    assert metrics["cpc"] is None
    assert metrics["cost_per_registration"] is None
    assert metrics["cost_per_ftd"] is None
    # Безвалютные метрики не деньги — прятать их нельзя никогда.
    assert metrics["impressions"] == 100
    assert metrics["clicks"] == 10
    assert metrics["registrations"] == 1
    assert metrics["ftd"] == 0
    assert metrics["confirmed_deposits"] == 0
    assert metrics["frequency"] == "1.20"


def test_redact_approaching_stop_row_does_not_reintroduce_the_disappearance_bug() -> None:
    """Regression: hiding the percent (#353) on the raw dict *before*

    `_approaching_stop_section` selects rows would make its own "no percent
    -> drop" rule swallow the row, silently emptying the early-warning feed
    for every currency-unconfirmed cabinet — the same failure shape #352
    fixes, for a different reason. Redaction must happen after selection.
    """
    now = datetime(2026, 8, 14, 10, 0, 30, tzinfo=UTC)
    row = _approaching_row(row_id="hidden-money", percent_to_stop="84.00")

    section = _approaching_stop_section(rows=[row], now=now)
    assert section.data is not None
    item = section.data.items[0]

    redacted = _redact_approaching_stop_row(item)

    assert redacted.id == "hidden-money"
    assert redacted.rule_context.percent_to_stop is None
    assert redacted.rule_context.value is None
    assert redacted.rule_context.threshold is None
    assert redacted.metrics.spend is None
    # Клики/показы не денежные — прятать их нельзя.
    assert redacted.metrics.clicks == 10
    assert redacted.metrics.impressions == 100


def test_approaching_stop_empty_list_is_empty_not_unavailable() -> None:
    section = _approaching_stop_section(
        rows=[],
        now=datetime(2026, 8, 14, 10, tzinfo=UTC),
    )

    assert section.state == "empty"
    assert section.data is not None
    assert section.data.items == []


def test_forward_migration_is_idempotent_and_irreversible() -> None:
    migration = importlib.import_module("migrations.versions.0002_operator_rule_context")
    source_path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "0002_operator_rule_context.py"
    )
    source = source_path.read_text(encoding="utf-8")

    assert migration.down_revision == "0001_safety_first_baseline"
    assert source.count("ADD COLUMN IF NOT EXISTS nearest_rule_") == 4
    with pytest.raises(RuntimeError, match="forward-only"):
        migration.downgrade()

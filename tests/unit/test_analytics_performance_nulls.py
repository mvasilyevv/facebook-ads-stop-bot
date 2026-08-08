from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from inspect import getsource

from apps.api.routers.v1.analytics import _instant_window, _section_quality
from apps.api.routers.v1.schemas.operator import DataState
from core.adset_pro.reconciliation import DEFAULT_PROVIDER_LOOKBACK
from core.analytics import DEFAULT_ANALYTICS_WINDOW
from core.analytics.performance import aggregate_performance, fetch_performance_rows


def _row(**overrides):
    values = {
        "campaign_id": uuid.uuid4(),
        "fb_campaign_id": "campaign-1",
        "campaign_name": "Campaign",
        "adset_id": uuid.uuid4(),
        "fb_adset_id": "adset-1",
        "adset_name": "Ad set",
        "ad_id": uuid.uuid4(),
        "fb_ad_id": "ad-1",
        "ad_name": "Ad",
        "ad_account_id": "123",
        "cabinet_timezone": "UTC",
        "timezone_known": True,
        "offer_id": uuid.uuid4(),
        "offer_code": "OFFER",
        "cpa_threshold": Decimal("10"),
        "stop_percent_of_rule": Decimal("80"),
        "spend": Decimal("0"),
        "impressions": 0,
        "clicks": 0,
        "leads": 0,
        "registrations": 0,
        "ftds": 0,
        "confirmed_deposits": 0,
        "redeposits": 0,
        "revenue": Decimal("0"),
        "meta_available": True,
        "tracker_state_available": True,
        "tracker_events_available": True,
        "account_currency_complete": True,
        "rule_currency_complete": True,
        "tracker_currency_complete": True,
    }
    values.update(overrides)
    return values


def _aggregate(rows):
    return aggregate_performance(
        rows,
        level="campaign",
        is_live=True,
        sort="spend",
        direction="desc",
        page=1,
        page_size=50,
    )


def test_out_of_range_page_is_clamped_to_last_available_page() -> None:
    first_campaign_id = uuid.uuid4()
    second_campaign_id = uuid.uuid4()

    result = aggregate_performance(
        [
            _row(
                campaign_id=first_campaign_id,
                fb_campaign_id="campaign-1",
                spend=Decimal("20"),
            ),
            _row(
                campaign_id=second_campaign_id,
                fb_campaign_id="campaign-2",
                spend=Decimal("10"),
            ),
        ],
        level="campaign",
        is_live=True,
        sort="spend",
        direction="desc",
        page=999,
        page_size=1,
    )

    assert result["pagination"] == {
        "page": 2,
        "page_size": 1,
        "total": 2,
        "pages": 2,
    }
    assert [row["id"] for row in result["rows"]] == [str(second_campaign_id)]


def test_known_zero_is_preserved_only_with_source_evidence() -> None:
    result = _aggregate([_row()])

    row = result["rows"][0]
    assert row["state"] == "ready"
    assert row["cabinet_timezone"] == "UTC"
    assert row["timezone_known"] is True
    assert row["timezone_state"] == "single"
    assert row["spend"] == "0.00"
    assert row["clicks"] == 0
    assert row["registrations"] == 0
    assert row["revenue"] == "0.00"


def test_known_zero_revenue_produces_zero_roas_with_positive_spend() -> None:
    result = _aggregate([_row(spend=Decimal("10"), revenue=Decimal("0"))])

    row = result["rows"][0]
    assert row["state"] == "ready"
    assert row["revenue"] == "0.00"
    assert row["roas"] == "0.0000"
    assert row["roi_pct"] == "-100.00"
    assert result["totals"]["revenue"] == "0.00"
    assert result["totals"]["roas"] == "0.0000"


def test_unknown_event_revenue_keeps_roas_unknown_and_marks_partial() -> None:
    result = _aggregate([_row(spend=Decimal("10"), revenue=None)])

    row = result["rows"][0]
    assert row["state"] == "partial"
    assert row["revenue"] is None
    assert row["roas"] is None
    assert row["roi_pct"] is None
    assert "Источник вернул не все поля метрик" in row["issues"]
    assert result["totals"]["revenue"] is None
    assert result["totals"]["roas"] is None
    assert result["_quality"]["has_partial_rows"] is True


def test_unknown_cabinet_timezone_has_no_utc_or_per_account_fallback() -> None:
    row = _aggregate([_row(cabinet_timezone=None, timezone_known=False)])["rows"][0]

    assert row["cabinet_timezone"] is None
    assert row["timezone_known"] is False
    assert row["timezone_state"] == "unknown"


def test_missing_tracker_rows_stay_null_instead_of_becoming_zero() -> None:
    result = _aggregate(
        [
            _row(
                registrations=None,
                ftds=None,
                confirmed_deposits=None,
                redeposits=None,
                revenue=None,
                tracker_state_available=False,
                tracker_events_available=False,
            )
        ]
    )

    row = result["rows"][0]
    assert row["state"] == "partial"
    assert row["spend"] == "0.00"
    assert row["registrations"] is None
    assert row["ftds"] is None
    assert row["revenue"] is None
    assert row["live_budget"] is None
    assert result["totals"]["registrations"] is None


def test_one_missing_child_makes_group_metric_unknown() -> None:
    campaign_id = uuid.uuid4()
    result = _aggregate(
        [
            _row(campaign_id=campaign_id, spend=Decimal("12"), registrations=1),
            _row(
                campaign_id=campaign_id,
                ad_id=uuid.uuid4(),
                adset_id=uuid.uuid4(),
                spend=None,
                impressions=None,
                clicks=None,
                leads=None,
                meta_available=False,
            ),
        ]
    )

    row = result["rows"][0]
    assert row["state"] == "partial"
    assert row["spend"] is None
    assert row["clicks"] is None
    assert row["registrations"] == 1
    assert result["totals"]["spend"] is None


def test_catalog_row_without_any_evidence_is_unavailable_not_zero() -> None:
    result = _aggregate(
        [
            _row(
                spend=None,
                impressions=None,
                clicks=None,
                leads=None,
                registrations=None,
                ftds=None,
                confirmed_deposits=None,
                redeposits=None,
                revenue=None,
                meta_available=False,
                tracker_state_available=False,
                tracker_events_available=False,
            )
        ]
    )

    row = result["rows"][0]
    assert row["state"] == "unavailable"
    assert row["spend"] is None
    assert row["clicks"] is None
    assert row["registrations"] is None
    assert result["_quality"]["has_evidence"] is False


def test_section_state_distinguishes_empty_stale_and_unavailable() -> None:
    now = datetime.now(UTC)
    good_sources = {
        source: {
            "status": "good",
            "last_event_at": now,
            "lag_seconds": 0,
            "issues": [],
        }
        for source in ("meta", "tracker")
    }
    stale_sources = {
        **good_sources,
        "meta": {
            "status": "degraded",
            "last_event_at": now,
            "lag_seconds": 901,
            "issues": ["Meta stale"],
        },
    }
    missing_sources = {
        source: {
            "status": "missing" if source == "meta" else "unknown",
            "last_event_at": None,
            "lag_seconds": None,
            "issues": ["No evidence"],
        }
        for source in ("meta", "tracker")
    }

    assert (
        _section_quality(
            sources=good_sources,
            has_rows=False,
            has_evidence=False,
        ).state
        == DataState.EMPTY
    )
    assert (
        _section_quality(
            sources=stale_sources,
            has_rows=True,
            has_evidence=True,
        ).state
        == DataState.STALE
    )
    assert (
        _section_quality(
            sources=missing_sources,
            has_rows=False,
            has_evidence=False,
        ).state
        == DataState.UNAVAILABLE
    )


def test_analytics_reads_only_canonical_storage_event_types() -> None:
    source = getsource(fetch_performance_rows)

    for retired_alias in (
        "'redep'",
        "'cpa_redep'",
        "'reg'",
        "'signup'",
        "'decline'",
        "'declined'",
        "'rejected'",
        "'trash'",
        "'baddep'",
    ):
        assert retired_alias not in source


def test_internal_daypart_window_is_fully_covered_by_provider_reconciliation() -> None:
    to_dt = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    from_dt, resolved_to = _instant_window(None, to_dt.isoformat())

    assert resolved_to == to_dt
    assert resolved_to - from_dt == DEFAULT_ANALYTICS_WINDOW
    assert DEFAULT_PROVIDER_LOOKBACK >= DEFAULT_ANALYTICS_WINDOW

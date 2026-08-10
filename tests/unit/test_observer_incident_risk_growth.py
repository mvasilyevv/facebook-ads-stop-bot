"""Canonical warning risk-growth suppression semantics."""

from __future__ import annotations

from decimal import Decimal

from core.observer.writers import (
    _risk_growth_reached,
    _risk_ratio_from_incident_facts,
    _risk_ratio_from_metrics,
    _rule_codes_from_metrics,
)


def _metrics(value: str, threshold: str = "4") -> dict:
    return {
        "_hits": [
            {
                "code": "cpc_warn",
                "stage": "warning",
                "value": value,
                "threshold": threshold,
            },
            {
                "code": "ignored_stop",
                "stage": "stop",
                "value": "99",
                "threshold": "1",
            },
        ]
    }


def test_warning_risk_uses_dimensionless_value_over_threshold() -> None:
    metrics = _metrics("5")

    assert _risk_ratio_from_metrics(metrics, stage="warning") == Decimal("1.25")
    assert _rule_codes_from_metrics(metrics, stage="warning") == ("cpc_warn",)
    assert _risk_ratio_from_incident_facts({"metrics": metrics}) == Decimal("1.25")


def test_warning_snapshot_requires_cumulative_25_percent_growth() -> None:
    previous = Decimal("1.00")

    assert _risk_growth_reached(previous, Decimal("1.2499")) is False
    assert _risk_growth_reached(previous, Decimal("1.25")) is True
    assert _risk_growth_reached(previous, Decimal("1.50")) is True
    assert _risk_growth_reached(None, Decimal("2")) is False

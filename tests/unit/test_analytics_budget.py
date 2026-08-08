from decimal import Decimal

import pytest

from core.analytics.budget import calculate_live_budget


def _budget(**overrides):
    values = {
        "actual_spend": Decimal("12.00"),
        "cpa_threshold": Decimal("50.00"),
        "stop_percent_of_rule": Decimal("80"),
        "clicks": 0,
        "leads": 0,
        "registrations": 0,
        "confirmed_deposits": 0,
    }
    values.update(overrides)
    return calculate_live_budget(**values)


def test_click_stage_uses_one_cpc_guardrail_when_clicks_are_zero() -> None:
    budget = _budget()

    assert budget is not None
    assert budget.stage == "click"
    assert budget.quantity == 1
    assert budget.base_unit == Decimal("1.00")
    assert budget.stop_unit == Decimal("0.80")
    assert budget.base_delta == Decimal("11.00")


def test_click_stage_multiplies_base_by_clicks() -> None:
    budget = _budget(clicks=20)

    assert budget is not None
    assert budget.base_budget == Decimal("20.00")
    assert budget.stop_budget == Decimal("16.00")
    assert budget.base_delta == Decimal("-8.00")


def test_lead_stage_has_priority_over_clicks() -> None:
    budget = _budget(clicks=40, leads=3)

    assert budget is not None
    assert budget.stage == "lead"
    assert budget.base_unit == Decimal("5.00")
    assert budget.quantity == 3


def test_registration_stage_uses_tracker_registrations() -> None:
    budget = _budget(clicks=40, leads=3, registrations=2)

    assert budget is not None
    assert budget.stage == "registration"
    assert budget.base_unit == Decimal("10.00")
    assert budget.base_budget == Decimal("20.00")


def test_confirmed_deposit_stage_uses_single_cpa_cap() -> None:
    budget = _budget(registrations=7, confirmed_deposits=3)

    assert budget is not None
    assert budget.stage == "deposit"
    assert budget.quantity == 1
    assert budget.base_budget == Decimal("50.00")
    assert budget.stop_budget == Decimal("40.00")


def test_missing_cpa_returns_none_instead_of_fake_zero() -> None:
    assert _budget(cpa_threshold=None) is None


@pytest.mark.parametrize(
    "stop_percent",
    [None, Decimal("0"), Decimal("-1"), Decimal("101"), Decimal("NaN")],
)
def test_missing_or_unsafe_stop_percent_never_invents_a_budget(stop_percent) -> None:
    assert _budget(stop_percent_of_rule=stop_percent) is None

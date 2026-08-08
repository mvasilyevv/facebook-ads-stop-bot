"""Live budget economics derived from the canonical offer-rule ladder."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from core.rules.types import CPC_PERCENT_OF_CPA, CPL_PERCENT_OF_CPA, CPR_PERCENT_OF_CPA

_HUNDRED = Decimal("100")
_MONEY = Decimal("0.01")

BudgetStage = Literal["click", "lead", "registration", "deposit"]


@dataclass(frozen=True, slots=True)
class LiveBudget:
    """Budget expectation for one ad at its current funnel stage."""

    stage: BudgetStage
    base_unit: Decimal
    stop_unit: Decimal
    quantity: int
    base_budget: Decimal
    stop_budget: Decimal
    base_delta: Decimal
    stop_delta: Decimal


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(_MONEY, rounding=ROUND_HALF_UP)


def calculate_live_budget(
    *,
    actual_spend: Decimal,
    cpa_threshold: Decimal | None,
    stop_percent_of_rule: Decimal | None,
    clicks: int,
    leads: int,
    registrations: int,
    confirmed_deposits: int,
) -> LiveBudget | None:
    """Return live base/stop budget, or ``None`` without confirmed rule inputs.

    The stage deliberately follows the same funnel ordering as the stop evaluator.
    A confirmed deposit uses one CPA cap regardless of the number of deposits, which
    matches the current conservative deposit-stage rule.
    """
    if cpa_threshold is None or stop_percent_of_rule is None:
        return None

    cpa = Decimal(cpa_threshold)
    stop_percent = Decimal(stop_percent_of_rule)
    if (
        not cpa.is_finite()
        or cpa <= 0
        or not stop_percent.is_finite()
        or stop_percent <= 0
        or stop_percent > _HUNDRED
    ):
        return None

    if confirmed_deposits > 0:
        stage: BudgetStage = "deposit"
        base_unit = cpa
        quantity = 1
    elif registrations > 0:
        stage = "registration"
        base_unit = cpa * CPR_PERCENT_OF_CPA / _HUNDRED
        quantity = registrations
    elif leads > 0:
        stage = "lead"
        base_unit = cpa * CPL_PERCENT_OF_CPA / _HUNDRED
        quantity = leads
    else:
        stage = "click"
        base_unit = cpa * CPC_PERCENT_OF_CPA / _HUNDRED
        quantity = max(clicks, 1)

    base_unit = _money(base_unit)
    stop_unit = _money(base_unit * stop_percent / _HUNDRED)
    base_budget = _money(base_unit * quantity)
    stop_budget = _money(stop_unit * quantity)
    actual = _money(actual_spend)
    return LiveBudget(
        stage=stage,
        base_unit=base_unit,
        stop_unit=stop_unit,
        quantity=quantity,
        base_budget=base_budget,
        stop_budget=stop_budget,
        base_delta=_money(actual - base_budget),
        stop_delta=_money(actual - stop_budget),
    )


__all__ = ["BudgetStage", "LiveBudget", "calculate_live_budget"]

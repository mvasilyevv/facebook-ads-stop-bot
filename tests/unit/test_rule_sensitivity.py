# -*- coding: utf-8 -*-
"""Per-offer чувствительность правил + фиксация базовых процентов.

Базовые проценты (2/10/20% от CPA, 5 рег, 50-70/70-90%) — ФИКСИРОВАНЫ как константы.
Регулируются только два per-offer коэффициента: стоп = N% от правила, ворнинг = M% от стопа.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.observer.pipeline import build_rule_context
from core.observer.queries import OfferRules
from core.rules.types import (
    CPC_PERCENT_OF_CPA,
    CPL_PERCENT_OF_CPA,
    CPR_PERCENT_OF_CPA,
    REGS_NO_DEP_STOP_COUNT,
    SPEND_NO_DEP_FROM_PERCENT,
    SPEND_NO_DEP_TO_PERCENT,
    SPEND_WITH_DEP_FROM_PERCENT,
    SPEND_WITH_DEP_TO_PERCENT,
    RuleContext,
)


def _offer(*, cpa, stop_pct=None, warn_pct=None) -> OfferRules:
    return OfferRules(
        offer_id=None,
        code="X",
        name="X",
        cpa_threshold=cpa,
        frequency_threshold=None,
        stop_percent_of_rule=stop_pct,
        warning_percent_of_stop=warn_pct,
    )


# Базовые проценты правил зафиксированы как константы — это сами правила.
def test_base_percents_are_fixed_constants():
    assert CPC_PERCENT_OF_CPA == Decimal("2")
    assert CPL_PERCENT_OF_CPA == Decimal("10")
    assert CPR_PERCENT_OF_CPA == Decimal("20")
    assert REGS_NO_DEP_STOP_COUNT == 5
    assert (SPEND_NO_DEP_FROM_PERCENT, SPEND_NO_DEP_TO_PERCENT) == (Decimal("50"), Decimal("70"))
    assert (SPEND_WITH_DEP_FROM_PERCENT, SPEND_WITH_DEP_TO_PERCENT) == (
        Decimal("70"),
        Decimal("90"),
    )


# Базовый процент нельзя переопределить через конструктор (init=False — «без возможности изменить»).
def test_base_percent_not_overridable():
    with pytest.raises(TypeError):
        RuleContext(
            cpa_amount=Decimal("3"),
            warning_percent_of_stop=Decimal("80"),
            cpc_percent_stop=Decimal("99"),
        )


# Дефолт (нет offer_rules → None) даёт 80/80 — прежнее поведение: CPA $3 → CPC стоп $0.05, ворнинг $0.04.
def test_default_sensitivity_is_80_80():
    ctx = build_rule_context(_offer(cpa=Decimal("3")))
    assert ctx.cpc_base_stop_threshold == Decimal("0.06")
    assert ctx.cpc_stop_threshold == Decimal("0.05")
    assert ctx.cpc_warning_threshold == Decimal("0.04")


# Per-offer stop_percent_of_rule меняет порог стопа, база правила остаётся фиксированной.
def test_per_offer_stop_percent_changes_stop():
    # стоп 50% от правила: база $0.06 → стоп $0.03
    ctx = build_rule_context(
        _offer(cpa=Decimal("3"), stop_pct=Decimal("50"), warn_pct=Decimal("80"))
    )
    assert ctx.cpc_base_stop_threshold == Decimal("0.06")  # база НЕ изменилась
    assert ctx.cpc_stop_threshold == Decimal("0.03")  # 0.06 × 50%
    assert ctx.cpc_warning_threshold == Decimal("0.02")  # 0.03 × 80%


# Per-offer warning_percent_of_stop меняет порог ворнинга относительно стопа.
def test_per_offer_warning_percent_changes_warning():
    # стоп 100% (=база $0.06), ворнинг 50% → $0.03
    ctx = build_rule_context(
        _offer(cpa=Decimal("3"), stop_pct=Decimal("100"), warn_pct=Decimal("50"))
    )
    assert ctx.cpc_stop_threshold == Decimal("0.06")
    assert ctx.cpc_warning_threshold == Decimal("0.03")


# Чувствительность применяется ко ВСЕМ ступеням: CPL (10%) и CPR (20%) тоже масштабируются.
def test_sensitivity_applies_to_all_stages():
    ctx = build_rule_context(
        _offer(cpa=Decimal("10"), stop_pct=Decimal("50"), warn_pct=Decimal("80"))
    )
    # CPL база 10%×10=1.00 → стоп 0.50; CPR база 20%×10=2.00 → стоп 1.00
    assert ctx.cpl_base_stop_threshold == Decimal("1.00")
    assert ctx.cpl_stop_threshold == Decimal("0.50")
    assert ctx.cpr_base_stop_threshold == Decimal("2.00")
    assert ctx.cpr_stop_threshold == Decimal("1.00")

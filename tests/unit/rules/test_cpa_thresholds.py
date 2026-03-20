from __future__ import annotations

from decimal import Decimal

import pytest

from core.rules import build_threshold_pack


# Проверяет, что при CPA 5 долларов абсолютные пороги совпадают с бизнес-формулами.
def test_build_threshold_pack_for_five_dollar_cpa() -> None:
    thresholds = build_threshold_pack(Decimal("5.00"))

    assert thresholds.cpc_stop == Decimal("0.10")
    assert thresholds.cpl_stop == Decimal("0.50")
    assert thresholds.registration_stop == Decimal("1.00")
    assert thresholds.no_deposit_spend_stop == Decimal("2.50")
    assert thresholds.no_deposit_spend_audit_top == Decimal("3.50")
    assert thresholds.after_deposit_spend_stop == Decimal("3.50")
    assert thresholds.after_deposit_spend_audit_top == Decimal("4.50")


# Проверяет, что нулевой или отрицательный CPA отвергается как некорректная входная ставка.
@pytest.mark.parametrize("cpa", [Decimal("0"), Decimal("-1.00")])
def test_build_threshold_pack_rejects_non_positive_cpa(cpa: Decimal) -> None:
    with pytest.raises(ValueError, match="CPA должна быть больше нуля"):
        build_threshold_pack(cpa)

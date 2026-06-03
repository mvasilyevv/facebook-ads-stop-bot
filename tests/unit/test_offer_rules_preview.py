# -*- coding: utf-8 -*-
"""Partial-upsert правил (формы не затирают друг друга) + preview = расчёт автостопа."""

from __future__ import annotations

from decimal import Decimal

from apps.api.routers.v1.schemas.offers import OfferRuleUpsertIn
from core.rules.types import RuleContext


# Форма частоты шлёт ТОЛЬКО frequency — exclude_unset не тронет CPA/чувствительность.
def test_partial_frequency_only():
    body = OfferRuleUpsertIn(frequency_threshold=Decimal("4"))
    assert set(body.model_dump(exclude_unset=True)) == {"frequency_threshold"}


# Форма чувствительности шлёт ТОЛЬКО stop/warning — CPA и частота не затираются.
def test_partial_sensitivity_only():
    body = OfferRuleUpsertIn(
        stop_percent_of_rule=Decimal("83"), warning_percent_of_stop=Decimal("80")
    )
    assert set(body.model_dump(exclude_unset=True)) == {
        "stop_percent_of_rule",
        "warning_percent_of_stop",
    }


# Форма оффера шлёт ТОЛЬКО CPA — частота и чувствительность не затираются.
def test_partial_cpa_only():
    body = OfferRuleUpsertIn(cpa_threshold=Decimal("3"))
    assert set(body.model_dump(exclude_unset=True)) == {"cpa_threshold"}


# Preview-расчёт идентичен RuleContext (тому, по которому реально стопит observer).
def test_preview_matches_autostop_calc():
    ctx = RuleContext(
        cpa_amount=Decimal("3"),
        warning_percent_of_stop=Decimal("80"),
        stop_percent_of_base=Decimal("80"),
    )
    # CPC: 2% × $3 = $0.06 база → стоп $0.05 (×80%) → ворнинг $0.04 (×80%)
    assert ctx.cpc_base_stop_threshold == Decimal("0.06")
    assert ctx.cpc_stop_threshold == Decimal("0.05")
    assert ctx.cpc_warning_threshold == Decimal("0.04")


# Per-offer чувствительность в preview меняет стоп/ворнинг (база фиксирована).
def test_preview_sensitivity_changes_thresholds():
    ctx = RuleContext(
        cpa_amount=Decimal("3"),
        warning_percent_of_stop=Decimal("50"),
        stop_percent_of_base=Decimal("50"),
    )
    assert ctx.cpc_base_stop_threshold == Decimal("0.06")  # база НЕ меняется
    assert ctx.cpc_stop_threshold == Decimal("0.03")  # 0.06 × 50%
    assert ctx.cpc_warning_threshold == Decimal("0.02")  # 0.03 × 50% = 0.015 → 0.02

# -*- coding: utf-8 -*-
"""Тесты задачи #260: пороги стоп-правил читаются из настроек оффера, а не констант.

Три класса:
1. Гарды по исходникам core/rules/ — падают ассертом, если порог зашит константой.
2. Null-настройки дают ровно нынешнее поведение по каждому из восьми значений.
3. Изменённая настройка оффера меняет исход только своего правила.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from pathlib import Path

from core.observer.pipeline import build_rule_context
from core.observer.queries import OfferRules
from core.rules.types import (
    CPC_PERCENT_OF_CPA,
    CPL_PERCENT_OF_CPA,
    CPR_PERCENT_OF_CPA,
    MIN_RATIO_DENOMINATOR,
    REGS_NO_DEP_STOP_COUNT,
    SPEND_NO_DEP_FROM_PERCENT,
    SPEND_NO_DEP_TO_PERCENT,
    SPEND_WITH_DEP_FROM_PERCENT,
    SPEND_WITH_DEP_TO_PERCENT,
    RuleContext,
)

_RULES_DIR = Path(__file__).resolve().parents[2] / "core" / "rules"


# ─── Гарды по исходникам ───────────────────────────────────────────────────


def test_guard_evaluator_does_not_import_min_ratio_denominator() -> None:
    """MIN_RATIO_DENOMINATOR должен читаться из ctx, а не быть константой в evaluator."""
    source = (_RULES_DIR / "evaluator.py").read_text(encoding="utf-8")
    assert "MIN_RATIO_DENOMINATOR" not in source, (
        "MIN_RATIO_DENOMINATOR захардкожен в evaluator.py; "
        "должен читаться из RuleContext.min_ratio_denominator"
    )


def test_guard_types_cpc_percent_not_init_false_constant() -> None:
    """cpc_percent_stop не должен быть field(init=False, default=CPC_PERCENT_OF_CPA)."""
    source = (_RULES_DIR / "types.py").read_text(encoding="utf-8")
    assert "init=False, default=CPC_PERCENT_OF_CPA" not in source, (
        "cpc_percent_stop зашит константой через init=False; "
        "должен вычисляться из настраиваемого поля cpc_percent_of_cpa"
    )


def test_guard_types_cpl_percent_not_init_false_constant() -> None:
    source = (_RULES_DIR / "types.py").read_text(encoding="utf-8")
    assert "init=False, default=CPL_PERCENT_OF_CPA" not in source, (
        "cpl_percent_stop зашит константой через init=False"
    )


def test_guard_types_cpr_percent_not_init_false_constant() -> None:
    source = (_RULES_DIR / "types.py").read_text(encoding="utf-8")
    assert "init=False, default=CPR_PERCENT_OF_CPA" not in source, (
        "cpr_percent_stop зашит константой через init=False"
    )


def test_guard_offerrules_has_all_threshold_fields() -> None:
    """OfferRules должен иметь все девять настраиваемых полей порогов."""
    field_names = {f.name for f in dataclasses.fields(OfferRules)}
    expected = {
        "cpc_percent_of_cpa",
        "cpl_percent_of_cpa",
        "cpr_percent_of_cpa",
        "regs_no_dep_stop_count",
        "spend_no_dep_from_percent",
        "spend_no_dep_to_percent",
        "spend_with_dep_from_percent",
        "spend_with_dep_to_percent",
        "min_ratio_denominator",
    }
    missing = expected - field_names
    assert not missing, f"OfferRules не имеет полей: {missing}"


def test_guard_rulecontext_has_configurable_percent_fields() -> None:
    """RuleContext должен принимать cpc/cpl/cpr percent и min_ratio_denominator как init-поля."""
    init_fields = {f.name for f in dataclasses.fields(RuleContext) if f.init}
    expected = {
        "cpc_percent_of_cpa",
        "cpl_percent_of_cpa",
        "cpr_percent_of_cpa",
        "min_ratio_denominator",
    }
    missing = expected - init_fields
    assert not missing, f"RuleContext не имеет init-полей: {missing}"


# ─── Вспомогательные ───────────────────────────────────────────────────────


def _offer(
    *,
    cpa: Decimal,
    stop_pct: Decimal = Decimal("80"),
    warn_pct: Decimal = Decimal("80"),
    cpc_percent_of_cpa: Decimal | None = None,
    cpl_percent_of_cpa: Decimal | None = None,
    cpr_percent_of_cpa: Decimal | None = None,
    regs_no_dep_stop_count: int | None = None,
    spend_no_dep_from_percent: Decimal | None = None,
    spend_no_dep_to_percent: Decimal | None = None,
    spend_with_dep_from_percent: Decimal | None = None,
    spend_with_dep_to_percent: Decimal | None = None,
    min_ratio_denominator: int | None = None,
) -> OfferRules:
    return OfferRules(
        offer_id=None,
        code="X",
        name="X",
        cpa_threshold=cpa,
        currency="USD",
        frequency_threshold=None,
        stop_percent_of_rule=stop_pct,
        warning_percent_of_stop=warn_pct,
        cpc_percent_of_cpa=cpc_percent_of_cpa,
        cpl_percent_of_cpa=cpl_percent_of_cpa,
        cpr_percent_of_cpa=cpr_percent_of_cpa,
        regs_no_dep_stop_count=regs_no_dep_stop_count,
        spend_no_dep_from_percent=spend_no_dep_from_percent,
        spend_no_dep_to_percent=spend_no_dep_to_percent,
        spend_with_dep_from_percent=spend_with_dep_from_percent,
        spend_with_dep_to_percent=spend_with_dep_to_percent,
        min_ratio_denominator=min_ratio_denominator,
    )


def _ctx(offer: OfferRules) -> RuleContext:
    return build_rule_context(offer, account_currency="USD", currency_exponent=2)


# ─── Null-настройки → поведение не меняется ────────────────────────────────


def test_null_settings_give_default_cpc_percent() -> None:
    ctx = _ctx(_offer(cpa=Decimal("100")))
    assert ctx.cpc_percent_stop == CPC_PERCENT_OF_CPA


def test_null_settings_give_default_cpl_percent() -> None:
    ctx = _ctx(_offer(cpa=Decimal("100")))
    assert ctx.cpl_percent_stop == CPL_PERCENT_OF_CPA


def test_null_settings_give_default_cpr_percent() -> None:
    ctx = _ctx(_offer(cpa=Decimal("100")))
    assert ctx.cpr_percent_stop == CPR_PERCENT_OF_CPA


def test_null_settings_give_default_regs_no_dep_count() -> None:
    ctx = _ctx(_offer(cpa=Decimal("100")))
    assert ctx.regs_no_dep_stop_count == REGS_NO_DEP_STOP_COUNT


def test_null_settings_give_default_spend_no_dep_range() -> None:
    ctx = _ctx(_offer(cpa=Decimal("100")))
    assert ctx.spend_no_dep_from_percent == SPEND_NO_DEP_FROM_PERCENT
    assert ctx.spend_no_dep_to_percent == SPEND_NO_DEP_TO_PERCENT


def test_null_settings_give_default_spend_with_dep_range() -> None:
    ctx = _ctx(_offer(cpa=Decimal("100")))
    assert ctx.spend_with_dep_from_percent == SPEND_WITH_DEP_FROM_PERCENT
    assert ctx.spend_with_dep_to_percent == SPEND_WITH_DEP_TO_PERCENT


def test_null_settings_give_default_min_ratio_denominator() -> None:
    ctx = _ctx(_offer(cpa=Decimal("100")))
    assert ctx.min_ratio_denominator == MIN_RATIO_DENOMINATOR


# ─── Изменённая настройка меняет только своё правило ──────────────────────


def test_custom_cpc_percent_changes_only_cpc_base() -> None:
    ctx_custom = _ctx(_offer(cpa=Decimal("100"), cpc_percent_of_cpa=Decimal("5")))
    ctx_default = _ctx(_offer(cpa=Decimal("100")))

    assert ctx_custom.cpc_base_stop_threshold == Decimal("5.00")  # 5% от 100
    assert ctx_custom.cpc_percent_stop == Decimal("5")
    # CPL и CPR не изменились
    assert ctx_custom.cpl_base_stop_threshold == ctx_default.cpl_base_stop_threshold
    assert ctx_custom.cpr_base_stop_threshold == ctx_default.cpr_base_stop_threshold


def test_custom_cpl_percent_changes_only_cpl_base() -> None:
    ctx_custom = _ctx(_offer(cpa=Decimal("100"), cpl_percent_of_cpa=Decimal("15")))
    ctx_default = _ctx(_offer(cpa=Decimal("100")))

    assert ctx_custom.cpl_base_stop_threshold == Decimal("15.00")
    assert ctx_custom.cpc_base_stop_threshold == ctx_default.cpc_base_stop_threshold
    assert ctx_custom.cpr_base_stop_threshold == ctx_default.cpr_base_stop_threshold


def test_custom_cpr_percent_changes_only_cpr_base() -> None:
    ctx_custom = _ctx(_offer(cpa=Decimal("100"), cpr_percent_of_cpa=Decimal("30")))
    ctx_default = _ctx(_offer(cpa=Decimal("100")))

    assert ctx_custom.cpr_base_stop_threshold == Decimal("30.00")
    assert ctx_custom.cpc_base_stop_threshold == ctx_default.cpc_base_stop_threshold
    assert ctx_custom.cpl_base_stop_threshold == ctx_default.cpl_base_stop_threshold


def test_custom_regs_no_dep_count_changes_only_regs_rule() -> None:
    ctx_custom = _ctx(_offer(cpa=Decimal("100"), regs_no_dep_stop_count=10))
    ctx_default = _ctx(_offer(cpa=Decimal("100")))

    assert ctx_custom.regs_no_dep_stop_count == 10
    assert ctx_custom.cpc_percent_stop == ctx_default.cpc_percent_stop
    assert ctx_custom.cpl_percent_stop == ctx_default.cpl_percent_stop


def test_custom_spend_no_dep_range_changes_only_spend_no_dep() -> None:
    ctx_custom = _ctx(
        _offer(
            cpa=Decimal("100"),
            spend_no_dep_from_percent=Decimal("40"),
            spend_no_dep_to_percent=Decimal("60"),
        )
    )
    ctx_default = _ctx(_offer(cpa=Decimal("100")))

    assert ctx_custom.spend_no_dep_from_percent == Decimal("40")
    assert ctx_custom.spend_no_dep_to_percent == Decimal("60")
    # spend_with_dep не изменился
    assert ctx_custom.spend_with_dep_from_percent == ctx_default.spend_with_dep_from_percent
    assert ctx_custom.spend_with_dep_to_percent == ctx_default.spend_with_dep_to_percent


def test_custom_spend_with_dep_range_changes_only_spend_with_dep() -> None:
    ctx_custom = _ctx(
        _offer(
            cpa=Decimal("100"),
            spend_with_dep_from_percent=Decimal("60"),
            spend_with_dep_to_percent=Decimal("80"),
        )
    )
    ctx_default = _ctx(_offer(cpa=Decimal("100")))

    assert ctx_custom.spend_with_dep_from_percent == Decimal("60")
    assert ctx_custom.spend_with_dep_to_percent == Decimal("80")
    assert ctx_custom.spend_no_dep_from_percent == ctx_default.spend_no_dep_from_percent
    assert ctx_custom.spend_no_dep_to_percent == ctx_default.spend_no_dep_to_percent


def test_custom_min_ratio_denominator_changes_only_min_ratio() -> None:
    ctx_custom = _ctx(_offer(cpa=Decimal("100"), min_ratio_denominator=50))
    ctx_default = _ctx(_offer(cpa=Decimal("100")))

    assert ctx_custom.min_ratio_denominator == 50
    assert ctx_custom.cpc_percent_stop == ctx_default.cpc_percent_stop
    assert ctx_custom.regs_no_dep_stop_count == ctx_default.regs_no_dep_stop_count


def test_settings_from_offer_a_do_not_affect_offer_b() -> None:
    """Настройки одного оффера не влияют на другой."""
    offer_a = _offer(cpa=Decimal("100"), cpc_percent_of_cpa=Decimal("5"))
    offer_b = _offer(cpa=Decimal("100"))  # без кастомных настроек

    ctx_a = _ctx(offer_a)
    ctx_b = _ctx(offer_b)

    assert ctx_a.cpc_percent_stop == Decimal("5")
    assert ctx_b.cpc_percent_stop == CPC_PERCENT_OF_CPA  # дефолт = 2%

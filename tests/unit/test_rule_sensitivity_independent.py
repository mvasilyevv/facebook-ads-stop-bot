# -*- coding: utf-8 -*-
"""Чувствительность каждого правила независима от остальных.

Тесты isolation_* должны УПАСТЬ по ассерту до правки:
при изменении cpr_warning_percent_of_stop правила spend_no_dep и spend_with_dep
не должны менять поведение — но сейчас меняют, потому что все три ссылаются на
effective_cpr_warning_percent_of_stop.

Тесты regression_* фиксируют, что при дефолтных значениях исходы по всем четырём
правилам остались прежними после разделения.
"""

from __future__ import annotations

from decimal import Decimal

from core.domain import AlertStage
from core.rules.evaluator import evaluate_stop_rules
from core.rules.types import RuleContext
from core.scanner.models import ScannedAdRow


def _make_row(**kwargs) -> ScannedAdRow:
    defaults = {
        "fb_ad_id": "120000000000000001",
        "campaign_name": "campaign",
        "adset_name": "adset",
        "ad_name": "test_ad",
        "delivery_status": "ACTIVE",
        "spend": Decimal("0.00"),
        "clicks": 0,
        "cpc": None,
        "leads": 0,
        "cost_per_lead": None,
        "registrations": 0,
        "cost_per_registration": None,
        "deposits": 0,
    }
    defaults.update(kwargs)
    return ScannedAdRow(**defaults)


def _make_ctx(**kwargs) -> RuleContext:
    """CPA=100 USD, stop_percent_of_base=100%, warning=80% — все дефолты явные."""
    defaults = {
        "currency": "USD",
        "currency_exponent": 2,
        "cpa_amount": Decimal("100.00"),
        "warning_percent_of_stop": Decimal("80"),
        "stop_percent_of_base": Decimal("100"),
    }
    defaults.update(kwargs)
    return RuleContext(**defaults)


# ── ISOLATION: падают до правки по ассерту ──────────────────────────────────


def test_isolation_cpr_warning_does_not_affect_spend_no_dep() -> None:
    """cpr_warning_percent_of_stop=20% не должен влиять на spend_no_dep.

    Математика при CPA=100, stop_percent_of_base=100%:
      spend_no_dep_from=50% → effective_from = 50 × 100/100 = 50 (STOP-порог)
      global warning=80%  → warning_from = 50 × 80/100  = 40 → spend=20 < 40 → нет алерта
      cpr_warning=20%     → warning_from = 50 × 20/100  = 10 → spend=20 > 10 → WARNING (баг!)
    """
    row = _make_row(spend=Decimal("20.00"), registrations=2)
    # cost_per_registration=None → _should_apply_registration_spend_guardrail=True

    ctx_default = _make_ctx()
    ctx_cpr_tight = _make_ctx(cpr_warning_percent_of_stop=Decimal("20"))

    result_default = evaluate_stop_rules(row, ctx_default)
    result_cpr_tight = evaluate_stop_rules(row, ctx_cpr_tight)

    # оба должны дать одинаковый исход: spend_no_dep не срабатывает
    assert result_default.stage is None
    assert result_cpr_tight.stage == result_default.stage, (
        f"spend_no_dep изменил поведение из-за cpr_warning_percent_of_stop: "
        f"default={result_default.stage}, cpr_tight={result_cpr_tight.stage}"
    )


def test_isolation_cpr_warning_does_not_affect_spend_with_dep() -> None:
    """cpr_warning_percent_of_stop=20% не должен влиять на spend_with_dep.

    Математика при CPA=100, stop_percent_of_base=100%, external_deposits=1:
      spend_with_dep_from=70% → effective_from = 70 × 100/100 = 70 (STOP-порог)
      global warning=80%  → warning_from = 70 × 80/100  = 56 → spend=30 < 56 → нет алерта
      cpr_warning=20%     → warning_from = 70 × 20/100  = 14 → spend=30 > 14 → WARNING (баг!)
    """
    row = _make_row(spend=Decimal("30.00"))

    ctx_default = _make_ctx(external_deposits=1)
    ctx_cpr_tight = _make_ctx(external_deposits=1, cpr_warning_percent_of_stop=Decimal("20"))

    result_default = evaluate_stop_rules(row, ctx_default)
    result_cpr_tight = evaluate_stop_rules(row, ctx_cpr_tight)

    assert result_default.stage is None
    assert result_cpr_tight.stage == result_default.stage, (
        f"spend_with_dep изменил поведение из-за cpr_warning_percent_of_stop: "
        f"default={result_default.stage}, cpr_tight={result_cpr_tight.stage}"
    )


# ── INDEPENDENCE: spend-правила можно настроить независимо ──────────────────


def test_spend_no_dep_has_own_warning_sensitivity() -> None:
    """spend_no_dep_warning_percent_of_stop действует только на spend_no_dep."""
    row = _make_row(spend=Decimal("20.00"), registrations=2)

    ctx_base = _make_ctx()
    ctx_spend_tight = _make_ctx(spend_no_dep_warning_percent_of_stop=Decimal("20"))

    result_base = evaluate_stop_rules(row, ctx_base)
    result_spend_tight = evaluate_stop_rules(row, ctx_spend_tight)

    # при tight spend_no_dep sensitivity: 20 > 10 → WARNING
    assert result_base.stage is None
    assert result_spend_tight.stage == AlertStage.WARNING
    assert "spend_no_dep_range" in result_spend_tight.warning_rule_codes


def test_spend_with_dep_has_own_warning_sensitivity() -> None:
    """spend_with_dep_warning_percent_of_stop действует только на spend_with_dep."""
    row = _make_row(spend=Decimal("30.00"))

    ctx_base = _make_ctx(external_deposits=1)
    ctx_spend_tight = _make_ctx(
        external_deposits=1,
        spend_with_dep_warning_percent_of_stop=Decimal("20"),
    )

    result_base = evaluate_stop_rules(row, ctx_base)
    result_spend_tight = evaluate_stop_rules(row, ctx_spend_tight)

    assert result_base.stage is None
    assert result_spend_tight.stage == AlertStage.WARNING
    assert "spend_with_dep_range" in result_spend_tight.warning_rule_codes


def test_spend_no_dep_sensitivity_does_not_affect_cpr() -> None:
    """spend_no_dep_warning_percent_of_stop не трогает порог предупреждения CPR."""
    # CPA=100, cpr_base=20, cpr_stop=20 (100%), cpr_warning at 80% = 16
    # Проверяем: при spend_no_dep_warning=20% cpr_warning остаётся 16, не 4
    ctx = _make_ctx(spend_no_dep_warning_percent_of_stop=Decimal("20"))
    assert ctx.cpr_warning_threshold == Decimal("16.00")


def test_spend_with_dep_sensitivity_does_not_affect_cpr() -> None:
    """spend_with_dep_warning_percent_of_stop не трогает порог предупреждения CPR."""
    ctx = _make_ctx(spend_with_dep_warning_percent_of_stop=Decimal("20"))
    assert ctx.cpr_warning_threshold == Decimal("16.00")


# ── REGRESSION: дефолтные значения дают прежний исход ──────────────────────


def test_regression_cpr_fires_at_stop_threshold() -> None:
    """CPR: стоп при cost_per_registration >= cpr_stop_threshold (20% × CPA × stop%)."""
    # CPA=100, stop=100% → cpr_stop = 20; cost_per_registration=25 → STOP
    row = _make_row(
        spend=Decimal("25.00"),
        registrations=1,
        cost_per_registration=Decimal("25.00"),
    )
    result = evaluate_stop_rules(row, _make_ctx())
    assert result.stage == AlertStage.STOP
    assert "cpr_stop" in result.stop_rule_codes


def test_regression_regs_no_dep_fires_at_stop_count() -> None:
    """regs_no_dep: стоп при 5 регистрациях без депозитов (дефолт REGS_NO_DEP_STOP_COUNT=5)."""
    row = _make_row(spend=Decimal("10.00"), registrations=5)
    result = evaluate_stop_rules(row, _make_ctx())
    assert result.stage == AlertStage.STOP
    assert "regs_no_dep_stop" in result.stop_rule_codes


def test_regression_spend_no_dep_fires_at_stop_threshold() -> None:
    """spend_no_dep: стоп при расходе >= 50% CPA (с stop_percent_of_base=100%)."""
    # effective_from = 50 × 100/100 = 50; spend=50 >= 50 → STOP
    row = _make_row(spend=Decimal("50.00"), registrations=2)
    result = evaluate_stop_rules(row, _make_ctx())
    assert result.stage == AlertStage.STOP
    assert "spend_no_dep_range" in result.stop_rule_codes


def test_regression_spend_with_dep_fires_at_stop_threshold() -> None:
    """spend_with_dep: стоп при расходе >= 70% CPA (с stop_percent_of_base=100%)."""
    # effective_from = 70 × 100/100 = 70; spend=70 >= 70 → STOP
    row = _make_row(spend=Decimal("70.00"))
    result = evaluate_stop_rules(row, _make_ctx(external_deposits=1))
    assert result.stage == AlertStage.STOP
    assert "spend_with_dep_range" in result.stop_rule_codes

# -*- coding: utf-8 -*-
"""Unit-тесты движка правил evaluator: все 6 стоп-метрик + spend-without-click нюанс."""

from __future__ import annotations

from decimal import Decimal

from core.domain import AlertStage
from core.rules.evaluator import evaluate_stop_rules
from core.rules.types import RuleContext
from core.scanner.models import ScannedAdRow


def _make_row(**kwargs) -> ScannedAdRow:
    """Хелпер для создания строки с дефолтами."""
    defaults = {
        "fb_ad_id": "123456",
        "campaign_name": "test_campaign",
        "adset_name": "test_adset",
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
    """Хелпер для создания контекста с CPA=5$."""
    defaults = {
        "cpa_amount": Decimal("5.00"),
        "warning_percent_of_stop": Decimal("80"),
    }
    defaults.update(kwargs)
    return RuleContext(**defaults)


# === Правило 1: CPC > 2% CPA ===


# Клик в пределах нормы → без алертов
def test_cpc_within_limit():
    row = _make_row(spend=Decimal("0.05"), clicks=1, cpc=Decimal("0.05"))
    ctx = _make_ctx()
    result = evaluate_stop_rules(row, ctx)
    assert result.stage is None


# CPC превышает стоп-порог (>0.10 при CPA=5$) → STOP
def test_cpc_exceeds_stop_threshold():
    row = _make_row(spend=Decimal("0.15"), clicks=1, cpc=Decimal("0.15"))
    ctx = _make_ctx()
    result = evaluate_stop_rules(row, ctx)
    assert result.stage == AlertStage.STOP
    assert any(h.code == "cpc_stop" for h in result.stop_hits)


# CPC в зоне предупреждения (0.08-0.10 при CPA=5$) → WARNING
def test_cpc_in_warning_zone():
    row = _make_row(spend=Decimal("0.09"), clicks=1, cpc=Decimal("0.09"))
    ctx = _make_ctx()
    result = evaluate_stop_rules(row, ctx)
    assert result.stage == AlertStage.WARNING
    assert any(h.code == "cpc_stop" for h in result.warning_hits)


# === Нюанс: spend > порога при clicks=0 → стоп ===


# Расход 0.12 при кликах=0 → первый клик будет > 0.10 → STOP
def test_spend_without_click_triggers_stop():
    row = _make_row(spend=Decimal("0.12"), clicks=0, cpc=None)
    ctx = _make_ctx()
    result = evaluate_stop_rules(row, ctx)
    assert result.stage == AlertStage.STOP
    assert any(h.code == "cpc_stop" for h in result.stop_hits)


# Расход 0.09 при кликах=0 → приближается к порогу → WARNING
def test_spend_without_click_triggers_warning():
    row = _make_row(spend=Decimal("0.09"), clicks=0, cpc=None)
    ctx = _make_ctx()
    result = evaluate_stop_rules(row, ctx)
    assert result.stage == AlertStage.WARNING
    assert any(h.code == "cpc_stop" for h in result.warning_hits)


# === Правило 2: CPL > 10% CPA ===


# Лид дороже порога (>0.50 при CPA=5$) → STOP
def test_cpl_exceeds_stop():
    row = _make_row(
        spend=Decimal("0.60"),
        leads=1,
        cost_per_lead=Decimal("0.60"),
        clicks=3,
        cpc=Decimal("0.03"),
    )
    ctx = _make_ctx()
    result = evaluate_stop_rules(row, ctx)
    assert result.stage == AlertStage.STOP
    assert any(h.code == "cpl_stop" for h in result.stop_hits)


# Расход > порога лида (0.50), но лидов нет → STOP (spend-without-lead)
def test_spend_without_lead_triggers_stop():
    row = _make_row(
        spend=Decimal("0.55"),
        leads=0,
        cost_per_lead=None,
        clicks=3,
        cpc=Decimal("0.03"),
    )
    ctx = _make_ctx()
    result = evaluate_stop_rules(row, ctx)
    assert result.stage == AlertStage.STOP
    assert any(h.code == "cpl_stop" for h in result.stop_hits)


# === Правило 3: CPR > 20% CPA ===


# Регистрация дороже 1.00 при CPA=5$ → STOP
def test_cpr_exceeds_stop():
    row = _make_row(
        spend=Decimal("1.50"),
        registrations=1,
        cost_per_registration=Decimal("1.50"),
        clicks=5,
        cpc=Decimal("0.03"),
        leads=1,
        cost_per_lead=Decimal("0.10"),
    )
    ctx = _make_ctx()
    result = evaluate_stop_rules(row, ctx)
    assert result.stage == AlertStage.STOP
    assert any(h.code == "cpr_stop" for h in result.stop_hits)


# === Правило 4: 5 рег без депозитов ===


# 5 регистраций, 0 депозитов → STOP
def test_five_regs_no_deposits_stop():
    row = _make_row(
        spend=Decimal("2.00"),
        registrations=5,
        deposits=0,
        cost_per_registration=Decimal("0.40"),
        clicks=10,
        cpc=Decimal("0.02"),
        leads=5,
        cost_per_lead=Decimal("0.10"),
    )
    ctx = _make_ctx()
    result = evaluate_stop_rules(row, ctx)
    assert result.stage == AlertStage.STOP
    assert any(h.code == "regs_no_dep_stop" for h in result.stop_hits)


# 4 регистрации, 0 депозитов → WARNING (80% от 5 = 4)
def test_four_regs_no_deposits_warning():
    row = _make_row(
        spend=Decimal("1.60"),
        registrations=4,
        deposits=0,
        cost_per_registration=Decimal("0.40"),
        clicks=8,
        cpc=Decimal("0.02"),
        leads=4,
        cost_per_lead=Decimal("0.10"),
    )
    ctx = _make_ctx()
    result = evaluate_stop_rules(row, ctx)
    assert result.stage == AlertStage.WARNING
    assert any(h.code == "regs_no_dep_stop" for h in result.warning_hits)


# 5 регистраций, 1 депозит → НЕ срабатывает (есть депозит)
def test_five_regs_with_deposit_no_trigger():
    row = _make_row(
        spend=Decimal("2.00"),
        registrations=5,
        deposits=1,
        cost_per_registration=Decimal("0.40"),
        clicks=10,
        cpc=Decimal("0.02"),
        leads=5,
        cost_per_lead=Decimal("0.10"),
    )
    ctx = _make_ctx()
    result = evaluate_stop_rules(row, ctx)
    # Правило 4 не должно сработать, т.к. deposits=1
    assert not any(h.code == "regs_no_dep_stop" for h in result.stop_hits)


# === Правило 5: Расход 50-70% CPA, 0 депов, рега в норме ===


# Расход 60% CPA=3.00, 0 депов, рега < порога → STOP
def test_spend_no_dep_range_stop():
    row = _make_row(
        spend=Decimal("3.00"),
        registrations=3,
        deposits=0,
        cost_per_registration=Decimal("0.50"),
        clicks=15,
        cpc=Decimal("0.02"),
        leads=5,
        cost_per_lead=Decimal("0.10"),
    )
    ctx = _make_ctx()
    result = evaluate_stop_rules(row, ctx)
    assert any(h.code == "spend_no_dep_range" for h in result.stop_hits)


# === Правило 6: Есть деп, расход 70-90% CPA ===


# Расход 80% CPA=4.00, 1 депозит → STOP
def test_spend_with_dep_range_stop():
    row = _make_row(
        spend=Decimal("4.00"),
        registrations=3,
        deposits=1,
        cost_per_registration=Decimal("0.50"),
        clicks=20,
        cpc=Decimal("0.02"),
        leads=5,
        cost_per_lead=Decimal("0.10"),
    )
    ctx = _make_ctx()
    result = evaluate_stop_rules(row, ctx)
    assert any(h.code == "spend_with_dep_range" for h in result.stop_hits)


# === Нет алертов — всё в норме ===


# Все метрики в допустимой зоне → stage=None
def test_all_metrics_ok():
    row = _make_row(
        spend=Decimal("0.04"),
        clicks=2,
        cpc=Decimal("0.02"),
        leads=1,
        cost_per_lead=Decimal("0.04"),
        registrations=1,
        cost_per_registration=Decimal("0.04"),
        deposits=0,
    )
    ctx = _make_ctx()
    result = evaluate_stop_rules(row, ctx)
    assert result.stage is None
    assert len(result.stop_hits) == 0
    assert len(result.warning_hits) == 0


# === Отключённое правило не срабатывает ===


# CPC > порога, но правило отключено → нет алерта
def test_disabled_rule_does_not_trigger():
    row = _make_row(spend=Decimal("0.15"), clicks=1, cpc=Decimal("0.15"))
    ctx = _make_ctx(cpc_enabled=False)
    result = evaluate_stop_rules(row, ctx)
    assert not any(h.code == "cpc_stop" for h in result.stop_hits)

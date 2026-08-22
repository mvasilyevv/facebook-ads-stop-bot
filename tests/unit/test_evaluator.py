# -*- coding: utf-8 -*-
"""Unit-тесты evaluator для лесенки funnel-логики и ранних сигналов."""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.domain import AlertStage
from core.rules.evaluator import evaluate_stop_rules
from core.rules.types import RuleContext
from core.scanner.models import ScannedAdRow


def _make_row(**kwargs) -> ScannedAdRow:
    """Создаёт строку объявления с безопасными дефолтами."""
    defaults = {
        "fb_ad_id": "120241979860890176",
        "campaign_name": "campaign",
        "adset_name": "adset",
        "ad_name": "DRC_CR2_CR015",
        "delivery_status": "ACTIVE",
        "spend": Decimal("0.00"),
        "clicks": 0,
        "cpc": None,
        "outbound_clicks": 0,
        "outbound_ctr": None,
        "landing_page_views": 0,
        "cost_per_landing_page_view": None,
        "cpm": None,
        "frequency": None,
        "leads": 0,
        "cost_per_lead": None,
        "registrations": 0,
        "cost_per_registration": None,
        "deposits": 0,
    }
    defaults.update(kwargs)
    return ScannedAdRow(**defaults)


def _make_ctx(**kwargs) -> RuleContext:
    """Создаёт контекст правил с CPA=5 и warning=80%."""
    defaults = {
        "currency": "USD",
        "currency_exponent": 2,
        "cpa_amount": Decimal("5.00"),
        "warning_percent_of_stop": Decimal("80"),
        "stop_percent_of_base": Decimal("100"),
    }
    defaults.update(kwargs)
    return RuleContext(**defaults)


def test_nearest_stop_selects_largest_ratio_across_enabled_rules() -> None:
    row = _make_row(
        spend=Decimal("0.35"),
        clicks=1,
        cpc=Decimal("0.05"),
        frequency=Decimal("2.10"),
    )
    ctx = _make_ctx(
        frequency_anomaly_enabled=True,
        frequency_current=row.frequency,
        frequency_warning_threshold=Decimal("2.80"),
        frequency_stop_threshold=Decimal("3.50"),
    )

    result = evaluate_stop_rules(row, ctx)

    assert result.stage is None
    assert result.nearest_stop is not None
    assert result.nearest_stop.code == "cpl_stop"
    assert result.nearest_stop.value == Decimal("0.35")
    assert result.nearest_stop.threshold == Decimal("0.50")
    assert result.nearest_stop.stage is None


def test_unknown_metric_is_not_treated_as_zero_progress() -> None:
    row = _make_row(
        spend=Decimal("0.00"),
        clicks=1,
        cpc=None,
    )
    ctx = _make_ctx(
        cpl_enabled=False,
        frequency_anomaly_enabled=False,
    )

    result = evaluate_stop_rules(row, ctx)

    assert result.stage is None
    assert result.nearest_stop is None


# Проверяем что на стадии клика срабатывает прямой STOP по дорогому CPC.
def test_click_stage_returns_cpc_stop():
    row = _make_row(spend=Decimal("0.15"), clicks=1, cpc=Decimal("0.15"))

    result = evaluate_stop_rules(row, _make_ctx())

    assert result.stage == AlertStage.STOP
    assert result.matched_rule_codes == ["cpc_stop"]


# Проверяем что точное попадание CPC в стоп-порог уже отключает объявление.
def test_click_stage_returns_cpc_stop_on_exact_threshold():
    row = _make_row(spend=Decimal("0.35"), clicks=4, cpc=Decimal("0.09"))

    result = evaluate_stop_rules(row, _make_ctx(stop_percent_of_base=Decimal("90")))

    assert result.stage == AlertStage.STOP
    assert result.matched_rule_codes == ["cpc_stop"]


# Проверяем что cent-level warning по CPC остаётся рабочим после округления до цента.
def test_click_stage_returns_cpc_warning_after_cent_rounding():
    row = _make_row(
        spend=Decimal("0.06"),
        clicks=1,
        cpc=Decimal("0.06"),
    )

    result = evaluate_stop_rules(row, _make_ctx(stop_percent_of_base=Decimal("80")))

    assert result.stage == AlertStage.WARNING
    assert result.matched_rule_codes == ["cpc_stop"]


@pytest.mark.parametrize("derived_cpc", [Decimal("0.0004"), Decimal("0.0005")])
def test_kwd_subminor_cpc_is_not_rounded_into_stop(derived_cpc: Decimal) -> None:
    row = _make_row(
        spend=Decimal("0.000"),
        clicks=1,
        cpc=derived_cpc,
    )
    ctx = _make_ctx(
        currency="KWD",
        currency_exponent=3,
        cpa_amount=Decimal("0.062"),
    )

    result = evaluate_stop_rules(row, ctx)

    assert ctx.cpc_stop_threshold == Decimal("0.001")
    assert result.stage is None


def test_currency_exponent_controls_money_summary_precision() -> None:
    row = _make_row(
        spend=Decimal("20"),
        clicks=1,
        cpc=Decimal("20"),
    )
    ctx = _make_ctx(
        currency="JPY",
        currency_exponent=0,
        cpa_amount=Decimal("1000"),
    )

    result = evaluate_stop_rules(row, ctx)

    summary = result.stop_hits[0].summary
    assert result.stage == AlertStage.STOP
    # Оператор должен прочитать метрику по-русски, с суммой и её валютой.
    assert summary.startswith("Цена клика ")
    assert "20 JPY" in summary
    assert ".00" not in summary
    assert "CPC" not in summary


def test_kwd_money_summary_preserves_third_decimal() -> None:
    row = _make_row(
        spend=Decimal("0.001"),
        clicks=1,
        cpc=Decimal("0.001"),
    )
    ctx = _make_ctx(
        currency="KWD",
        currency_exponent=3,
        cpa_amount=Decimal("0.062"),
    )

    result = evaluate_stop_rules(row, ctx)

    summary = result.stop_hits[0].summary
    assert result.stage == AlertStage.STOP
    assert summary.startswith("Цена клика ")
    assert "0.001 KWD" in summary


# Проверяем что отдельные проценты CPC переопределяют legacy-настройки только для шага клика.
def test_click_stage_uses_cpc_specific_thresholds():
    row = _make_row(
        spend=Decimal("0.06"),
        clicks=1,
        cpc=Decimal("0.06"),
    )

    result = evaluate_stop_rules(
        row,
        _make_ctx(
            warning_percent_of_stop=Decimal("95"),
            stop_percent_of_base=Decimal("100"),
            cpc_warning_percent_of_stop=Decimal("75"),
            cpc_stop_percent_of_base=Decimal("80"),
        ),
    )

    assert result.stage == AlertStage.WARNING
    assert result.matched_rule_codes == ["cpc_stop"]


# Проверяем что без кликов работает pre-click guardrail по расходу.
def test_click_stage_guardrail_without_clicks_triggers_stop():
    row = _make_row(spend=Decimal("0.12"), clicks=0, cpc=None)

    result = evaluate_stop_rules(row, _make_ctx())

    assert result.stage == AlertStage.STOP
    assert result.matched_rule_codes == ["cpc_stop"]


# Решение байера: гейт-минимум показов УБРАН — стопаем жёстко по расходу даже на
# 1-2 показах (расход без клика выше стоп-порога = money-сигнал, не ждём накопления).
def test_guardrail_fires_even_with_few_impressions():
    row = _make_row(spend=Decimal("0.12"), clicks=0, cpc=None)

    result = evaluate_stop_rules(row, _make_ctx(impressions=2))

    assert result.stage == AlertStage.STOP
    assert "cpc_stop" in result.matched_rule_codes


# Guardrail стопает при умеренных показах (гейт убран — число показов не важно).
def test_guardrail_fires_at_moderate_impressions():
    row = _make_row(spend=Decimal("0.12"), clicks=0, cpc=None)

    result = evaluate_stop_rules(row, _make_ctx(impressions=6))

    assert result.stage == AlertStage.STOP
    assert "cpc_stop" in result.matched_rule_codes


# Guardrail стопает и при больших показах — поведение не зависит от числа показов.
def test_guardrail_fires_at_high_impressions():
    row = _make_row(spend=Decimal("0.12"), clicks=0, cpc=None)

    result = evaluate_stop_rules(row, _make_ctx(impressions=500))

    assert result.stage == AlertStage.STOP
    assert result.matched_rule_codes == ["cpc_stop"]


# Проверяем что после кликов, но без лидов, расход может эскалировать в следующий порог лида.
def test_click_stage_escalates_to_lead_guardrail():
    row = _make_row(
        spend=Decimal("0.50"),
        clicks=10,
        cpc=Decimal("0.05"),
    )

    result = evaluate_stop_rules(row, _make_ctx())

    assert result.stage == AlertStage.STOP
    assert result.matched_rule_codes == ["cpl_stop"]


# Проверяем что STOP по следующей ступени лида перебивает WARNING по текущему CPC.
def test_click_stage_prioritizes_lead_stop_over_click_warning():
    row = _make_row(
        spend=Decimal("0.50"),
        clicks=7,
        cpc=Decimal("0.08"),
    )

    result = evaluate_stop_rules(row, _make_ctx())

    assert result.stage == AlertStage.STOP
    assert result.matched_rule_codes == ["cpl_stop"]


# Проверяем что наличие лида полностью подавляет правило клика.
def test_lead_stage_suppresses_click_rule():
    row = _make_row(
        spend=Decimal("0.20"),
        clicks=1,
        cpc=Decimal("0.20"),
        leads=1,
        cost_per_lead=Decimal("0.20"),
    )

    result = evaluate_stop_rules(row, _make_ctx())

    assert result.stage is None
    assert result.matched_rule_codes == []


# Проверяем что на стадии лида следующий spend guardrail сравнивается уже с порогом реги.
def test_lead_stage_escalates_to_registration_guardrail():
    row = _make_row(
        spend=Decimal("1.00"),
        clicks=10,
        cpc=Decimal("0.05"),
        leads=2,
        cost_per_lead=Decimal("0.20"),
    )

    result = evaluate_stop_rules(row, _make_ctx())

    assert result.stage == AlertStage.STOP
    assert result.matched_rule_codes == ["cpr_stop"]


# Проверяем что STOP по следующей ступени реги перебивает WARNING по текущему CPL.
def test_lead_stage_prioritizes_registration_stop_over_lead_warning():
    row = _make_row(
        spend=Decimal("1.00"),
        clicks=8,
        cpc=Decimal("0.06"),
        leads=1,
        cost_per_lead=Decimal("0.40"),
    )

    result = evaluate_stop_rules(row, _make_ctx())

    assert result.stage == AlertStage.STOP
    assert result.matched_rule_codes == ["cpr_stop"]


# Проверяем что на стадии реги CPR имеет приоритет над более поздними правилами без депов.
def test_registration_stage_prioritizes_cpr_before_other_rules():
    row = _make_row(
        spend=Decimal("3.00"),
        clicks=20,
        cpc=Decimal("0.05"),
        leads=5,
        cost_per_lead=Decimal("0.30"),
        registrations=5,
        cost_per_registration=Decimal("1.20"),
        deposits=0,
    )

    result = evaluate_stop_rules(row, _make_ctx())

    assert result.stage == AlertStage.STOP
    assert result.matched_rule_codes == ["cpr_stop"]


# Проверяем что STOP по регам без депа перебивает WARNING по текущему CPR.
def test_registration_stage_prioritizes_regs_without_dep_stop_over_cpr_warning():
    row = _make_row(
        spend=Decimal("2.00"),
        clicks=20,
        cpc=Decimal("0.05"),
        leads=5,
        cost_per_lead=Decimal("0.30"),
        registrations=5,
        cost_per_registration=Decimal("0.80"),
        deposits=0,
    )

    result = evaluate_stop_rules(row, _make_ctx())

    assert result.stage == AlertStage.STOP
    assert result.matched_rule_codes == ["regs_no_dep_stop"]


# Проверяем что per-rule cpr_warning_percent_of_stop влияет на spend-guardrail
# в регистрационной ступени. regs_no_dep_stop теперь использует фиксированный
# шаг stop-1 и при registrations=3 (< 4=stop-1) не срабатывает; вместо него
# spend_no_dep_range WARNING использует своё поле чувствительности, 50%:
# warning_from = 50% × 50% = 25%, текущий spend = 1.60/5.00×100 = 32% — в зоне.
def test_registration_stage_spend_no_dep_warning_pct_fires_warning():
    row = _make_row(
        spend=Decimal("1.60"),
        clicks=20,
        cpc=Decimal("0.05"),
        leads=5,
        cost_per_lead=Decimal("0.30"),
        registrations=3,
        cost_per_registration=Decimal("0.49"),
        deposits=0,
    )

    result = evaluate_stop_rules(
        row,
        _make_ctx(
            warning_percent_of_stop=Decimal("80"),
            spend_no_dep_warning_percent_of_stop=Decimal("50"),
        ),
    )

    assert result.stage == AlertStage.WARNING
    assert result.matched_rule_codes == ["spend_no_dep_range"]


# Проверяем что после нормальной цены реги включается правило 5 рег без депов раньше spend-range.
def test_registration_stage_prioritizes_regs_without_dep_before_spend_range():
    row = _make_row(
        spend=Decimal("3.00"),
        clicks=20,
        cpc=Decimal("0.05"),
        leads=5,
        cost_per_lead=Decimal("0.30"),
        registrations=5,
        cost_per_registration=Decimal("0.50"),
        deposits=0,
    )

    result = evaluate_stop_rules(row, _make_ctx())

    assert result.stage == AlertStage.STOP
    assert result.matched_rule_codes == ["regs_no_dep_stop"]


# Проверяем что STOP по spend-range без депа перебивает WARNING по CPR и по количеству рег.
def test_registration_stage_prioritizes_spend_without_dep_stop_over_warnings():
    row = _make_row(
        spend=Decimal("2.60"),
        clicks=20,
        cpc=Decimal("0.04"),
        leads=5,
        cost_per_lead=Decimal("0.25"),
        registrations=4,
        cost_per_registration=Decimal("0.80"),
        deposits=0,
    )

    result = evaluate_stop_rules(row, _make_ctx())

    assert result.stage == AlertStage.STOP
    assert result.matched_rule_codes == ["spend_no_dep_range"]


# Проверяем что spend-range без депа не молчит, когда расход улетает выше верхней границы диапазона.
def test_registration_stage_spend_without_dep_stop_above_upper_bound():
    row = _make_row(
        spend=Decimal("30.69"),
        clicks=264,
        cpc=Decimal("0.1200"),
        leads=5,
        cost_per_lead=Decimal("6.1400"),
        registrations=3,
        cost_per_registration=Decimal("0.50"),
        deposits=0,
    )

    result = evaluate_stop_rules(row, _make_ctx())

    assert result.stage == AlertStage.STOP
    assert result.matched_rule_codes == ["spend_no_dep_range"]


# Проверяем что spend-range без депов срабатывает только когда CPR ещё в норме.
def test_registration_stage_falls_back_to_spend_without_dep_warning():
    row = _make_row(
        spend=Decimal("2.20"),
        clicks=20,
        cpc=Decimal("0.04"),
        leads=5,
        cost_per_lead=Decimal("0.25"),
        registrations=3,
        cost_per_registration=Decimal("0.50"),
        deposits=0,
    )

    result = evaluate_stop_rules(row, _make_ctx())

    assert result.stage == AlertStage.WARNING
    assert result.matched_rule_codes == ["spend_no_dep_range"]


# После депозита остаётся только депозитная ступень; депозит теперь ТОЛЬКО из AdSet.pro.
def test_deposit_stage_uses_only_spend_with_dep_rule():
    row = _make_row(
        spend=Decimal("4.00"),
        clicks=20,
        cpc=Decimal("0.05"),
        leads=5,
        cost_per_lead=Decimal("0.30"),
        registrations=5,
        cost_per_registration=Decimal("2.00"),
        deposits=0,  # Meta-депозитов нет — стадию даёт внешний трекер
    )

    # external_deposits>=1 (AdSet.pro) → deposit_stage
    result = evaluate_stop_rules(row, _make_ctx(external_deposits=1))

    assert result.stage == AlertStage.STOP
    assert result.matched_rule_codes == ["spend_with_dep_range"]


# Meta-депозиты (row.deposits) БЕЗ AdSet.pro НЕ переводят в deposit_stage — источник только трекер.
def test_meta_deposits_alone_do_not_enter_deposit_stage():
    row = _make_row(
        spend=Decimal("4.00"),
        clicks=20,
        cpc=Decimal("0.05"),
        leads=5,
        cost_per_lead=Decimal("0.30"),
        registrations=5,
        cost_per_registration=Decimal("2.00"),
        deposits=3,  # Meta видит «депозиты», но AdSet.pro молчит
    )

    result = evaluate_stop_rules(row, _make_ctx(external_deposits=0))

    # не deposit_stage → spend_with_dep_range не среди матчей
    assert "spend_with_dep_range" not in result.matched_rule_codes


# Проверяем что депозит без регистрации не переводит объявление на deposit-stage.
def test_deposit_without_registration_stays_on_pre_registration_ladder():
    row = _make_row(
        spend=Decimal("0.50"),
        clicks=10,
        cpc=Decimal("0.05"),
        leads=0,
        registrations=0,
        deposits=1,
    )

    result = evaluate_stop_rules(row, _make_ctx())

    assert result.stage == AlertStage.STOP
    assert result.matched_rule_codes == ["cpl_stop"]


# Проверяем что CPM и частота сами по себе не создают алерт или ранний сигнал.
def test_cpm_and_frequency_are_diagnostics_only():
    row = _make_row(
        spend=Decimal("0.10"),
        clicks=5,
        cpc=Decimal("0.02"),
        cpm=Decimal("99.00"),
        frequency=Decimal("4.00"),
    )

    result = evaluate_stop_rules(row, _make_ctx())

    assert result.stage is None
    assert result.matched_rule_codes == []


# H1 (money-leak): при регистрациях без депозитов и НЕИЗВЕСТНОЙ цене реги
# (cost_per_registration=None — attribution-лаг Meta) spend-guardrail обязан
# работать. Спенд 60% CPA, 3 реги, 0 депов, CPR=None → STOP spend_no_dep_range.
def test_registration_stage_spend_guardrail_fires_when_cpr_unknown():
    row = _make_row(
        spend=Decimal("3.00"),  # 60% от CPA=5 → в стоп-диапазоне 50-70%
        clicks=20,
        cpc=Decimal("0.15"),
        leads=5,
        cost_per_lead=Decimal("0.60"),
        registrations=3,  # меньше regs_no_dep stop(5)/warn(4)
        cost_per_registration=None,  # цена реги ещё не посчитана Meta
    )

    result = evaluate_stop_rules(row, _make_ctx())

    assert result.stage is AlertStage.STOP
    assert "spend_no_dep_range" in result.stop_rule_codes


# Парность: тот же ад с ИЗВЕСТНОЙ нормальной ценой реги стопается так же —
# поведение при CPR=None не должно отличаться от CPR в норме.
def test_registration_stage_spend_guardrail_parity_known_vs_unknown_cpr():
    base = dict(
        spend=Decimal("3.00"),
        clicks=20,
        cpc=Decimal("0.15"),
        leads=5,
        cost_per_lead=Decimal("0.60"),
        registrations=3,
    )
    known = evaluate_stop_rules(
        _make_row(**base, cost_per_registration=Decimal("0.50")), _make_ctx()
    )
    unknown = evaluate_stop_rules(_make_row(**base, cost_per_registration=None), _make_ctx())

    assert known.stage is AlertStage.STOP
    assert unknown.stage == known.stage
    assert unknown.stop_rule_codes == known.stop_rule_codes


# Без ложных срабатываний: CPR=None + малый спенд (20% CPA) + мало рег → НЕ стоп.
def test_registration_stage_no_stop_on_low_spend_when_cpr_unknown():
    row = _make_row(
        spend=Decimal("1.00"),  # 20% от CPA=5 → ниже стоп-диапазона 50%
        clicks=10,
        cpc=Decimal("0.10"),
        leads=2,
        cost_per_lead=Decimal("0.50"),
        registrations=2,
        cost_per_registration=None,
    )

    result = evaluate_stop_rules(row, _make_ctx())

    assert result.stage is None

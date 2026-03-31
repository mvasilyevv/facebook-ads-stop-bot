# -*- coding: utf-8 -*-
"""Unit-тесты evaluator для лесенки funnel-логики и ранних сигналов."""

from __future__ import annotations

from decimal import Decimal

from core.domain import AlertStage, EnableRecommendationLevel
from core.rules.evaluator import determine_enable_recommendation_level, evaluate_stop_rules
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
        "cpa_amount": Decimal("5.00"),
        "warning_percent_of_stop": Decimal("80"),
        "stop_percent_of_base": Decimal("100"),
    }
    defaults.update(kwargs)
    return RuleContext(**defaults)


# Проверяем что на стадии клика срабатывает прямой STOP по дорогому CPC.
def test_click_stage_returns_cpc_stop():
    row = _make_row(spend=Decimal("0.15"), clicks=1, cpc=Decimal("0.15"))

    result = evaluate_stop_rules(row, _make_ctx())

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


# Проверяем что registration-stage правила используют отдельный CPR warning.
def test_registration_stage_uses_cpr_specific_warning_for_regs_without_dep():
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
            cpr_warning_percent_of_stop=Decimal("50"),
        ),
    )

    assert result.stage == AlertStage.WARNING
    assert result.matched_rule_codes == ["regs_no_dep_stop"]


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


# Проверяем что после первого депозита остаётся только депозитная ступень.
def test_deposit_stage_uses_only_spend_with_dep_rule():
    row = _make_row(
        spend=Decimal("4.00"),
        clicks=20,
        cpc=Decimal("0.05"),
        leads=5,
        cost_per_lead=Decimal("0.30"),
        registrations=5,
        cost_per_registration=Decimal("2.00"),
        deposits=1,
    )

    result = evaluate_stop_rules(row, _make_ctx())

    assert result.stage == AlertStage.STOP
    assert result.matched_rule_codes == ["spend_with_dep_range"]


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


# Проверяем что ранний сигнал по CTR исходящих кликов работает до лида и раньше funnel-warning.
def test_early_signal_outbound_ctr_triggers_before_leads():
    row = _make_row(
        spend=Decimal("0.30"),
        clicks=10,
        cpc=Decimal("0.03"),
        outbound_clicks=10,
        outbound_ctr=Decimal("0.50"),
    )

    result = evaluate_stop_rules(row, _make_ctx())

    assert result.stage == AlertStage.EARLY_SIGNAL
    assert result.matched_rule_codes == ["early_outbound_ctr_signal"]


# Проверяем что gate по минимальному расходу для Outbound CTR берётся из явной конфигурации.
def test_early_signal_outbound_ctr_respects_min_spend_gate():
    row = _make_row(
        spend=Decimal("0.30"),
        clicks=10,
        cpc=Decimal("0.03"),
        outbound_clicks=10,
        outbound_ctr=Decimal("0.50"),
    )

    result = evaluate_stop_rules(
        row,
        _make_ctx(
            early_outbound_ctr_signal_min_spend_percent=Decimal("7"),
            early_lpv_ratio_signal_enabled=False,
            early_cost_per_lpv_signal_enabled=False,
        ),
    )

    assert result.stage is None
    assert result.matched_rule_codes == []


# Проверяем что OFF-объявление с лидами и без рег/депов может получить OK-рекомендацию после выхода из стопа.
def test_enable_recommendation_returns_ok_for_lead_recovery_without_registration():
    row = _make_row(
        delivery_status="OFF",
        spend=Decimal("0.10"),
        clicks=5,
        cpc=Decimal("0.02"),
        leads=1,
        cost_per_lead=Decimal("0.20"),
        registrations=0,
        deposits=0,
    )
    ctx = _make_ctx()
    stop_evaluation = evaluate_stop_rules(row, ctx)

    result = determine_enable_recommendation_level(
        row,
        ctx,
        stop_evaluation=stop_evaluation,
    )

    assert stop_evaluation.stage is None
    assert result == EnableRecommendationLevel.OK


# Проверяем что OFF-объявление только с кликами тоже может получить OK после выхода из stop/warning.
def test_enable_recommendation_returns_ok_for_click_only_recovery():
    row = _make_row(
        delivery_status="OFF",
        spend=Decimal("0.08"),
        clicks=2,
        cpc=Decimal("0.04"),
        leads=0,
        registrations=0,
        deposits=0,
    )
    ctx = _make_ctx()

    result = determine_enable_recommendation_level(row, ctx)

    assert result == EnableRecommendationLevel.OK


# Проверяем что partial metrics на стадии регистрации блокируют recommendation даже без stop-сигнала.
def test_enable_recommendation_blocks_partial_registration_metrics():
    row = _make_row(
        delivery_status="OFF",
        spend=Decimal("0.80"),
        clicks=20,
        cpc=Decimal("0.04"),
        leads=4,
        cost_per_lead=Decimal("0.20"),
        registrations=2,
        cost_per_registration=None,
        deposits=0,
    )
    ctx = _make_ctx()

    result = determine_enable_recommendation_level(row, ctx)

    assert result is None


# Проверяем что подтверждённые регистрации с нормальным CPR дают безопасную OK-рекомендацию.
def test_enable_recommendation_returns_ok_for_confirmed_registration_recovery():
    row = _make_row(
        delivery_status="OFF",
        spend=Decimal("0.80"),
        clicks=20,
        cpc=Decimal("0.04"),
        leads=4,
        cost_per_lead=Decimal("0.20"),
        registrations=2,
        cost_per_registration=Decimal("0.40"),
        deposits=0,
    )
    ctx = _make_ctx()

    result = determine_enable_recommendation_level(row, ctx)

    assert result == EnableRecommendationLevel.OK


# Проверяем что депозит без регистрации не считается безопасным recovery-сигналом для включения.
def test_enable_recommendation_ignores_deposit_without_registration():
    row = _make_row(
        delivery_status="OFF",
        spend=Decimal("0.40"),
        clicks=20,
        cpc=Decimal("0.02"),
        leads=1,
        cost_per_lead=Decimal("0.20"),
        registrations=0,
        cost_per_registration=None,
        deposits=1,
    )
    ctx = _make_ctx()

    result = determine_enable_recommendation_level(row, ctx)

    assert result is None


# Проверяем, что OFF-объявление без расхода и без активности больше не получает OK-рекомендацию.
def test_enable_recommendation_blocks_zero_spend_off_ad():
    row = _make_row(
        delivery_status="OFF",
        spend=Decimal("0"),
        clicks=0,
        cpc=None,
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
    )
    ctx = _make_ctx()

    result = determine_enable_recommendation_level(row, ctx)

    assert result is None


# Проверяем что ранний сигнал по доходимости до лендинга срабатывает отдельно от warning/stop.
def test_early_signal_lpv_ratio_triggers_before_leads():
    row = _make_row(
        spend=Decimal("0.20"),
        clicks=10,
        cpc=Decimal("0.02"),
        outbound_clicks=10,
        landing_page_views=3,
    )

    result = evaluate_stop_rules(row, _make_ctx())

    assert result.stage == AlertStage.EARLY_SIGNAL
    assert result.matched_rule_codes == ["early_lpv_ratio_signal"]


# Проверяем что gate по количеству исходящих кликов для LPV ratio тоже задаётся явно.
def test_early_signal_lpv_ratio_respects_min_outbound_clicks_gate():
    row = _make_row(
        spend=Decimal("0.20"),
        clicks=10,
        cpc=Decimal("0.02"),
        outbound_clicks=10,
        landing_page_views=3,
    )

    result = evaluate_stop_rules(
        row,
        _make_ctx(early_lpv_ratio_signal_min_outbound_clicks=12),
    )

    assert result.stage is None
    assert result.matched_rule_codes == []


# Проверяем что ранний сигнал по цене LPV срабатывает только на ранней стадии.
def test_early_signal_cost_per_lpv_triggers_before_leads():
    row = _make_row(
        spend=Decimal("0.30"),
        clicks=10,
        cpc=Decimal("0.03"),
        outbound_clicks=4,
        landing_page_views=2,
        cost_per_landing_page_view=Decimal("0.30"),
    )

    result = evaluate_stop_rules(row, _make_ctx())

    assert result.stage == AlertStage.EARLY_SIGNAL
    assert result.matched_rule_codes == ["early_cost_per_lpv_signal"]


# Проверяем что gate по минимальному числу LPV не зашит и читается из конфигурации.
def test_early_signal_cost_per_lpv_respects_min_views_gate():
    row = _make_row(
        spend=Decimal("0.30"),
        clicks=10,
        cpc=Decimal("0.03"),
        outbound_clicks=4,
        landing_page_views=2,
        cost_per_landing_page_view=Decimal("0.30"),
    )

    result = evaluate_stop_rules(
        row,
        _make_ctx(early_cost_per_lpv_signal_min_views=3),
    )

    assert result.stage is None
    assert result.matched_rule_codes == []


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


# Проверяем что при наличии лида ранние сигналы полностью подавляются более глубокой стадией.
def test_early_signals_are_suppressed_after_first_lead():
    row = _make_row(
        spend=Decimal("0.30"),
        clicks=10,
        cpc=Decimal("0.03"),
        outbound_clicks=10,
        outbound_ctr=Decimal("0.50"),
        leads=1,
        cost_per_lead=Decimal("0.30"),
    )

    result = evaluate_stop_rules(row, _make_ctx())

    assert result.stage is None
    assert result.matched_rule_codes == []

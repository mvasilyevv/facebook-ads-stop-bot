# -*- coding: utf-8 -*-
"""Property-based тесты для core/rules/evaluator.py через Hypothesis.

HIGH #5 из backend_test_audit_round_8: property-based тестирование полностью
отсутствовало. Hypothesis проверяет инварианты evaluate_stop_rules на случайных
метриках — это находит граничные случаи, которые ручные тесты пропускают.

Проверяем 4 инварианта:
1. При spend=0 никогда не trigger ни warning ни stop.
2. Результат детерминирован для одного и того же input.
3. stage ∈ {None, WARNING, STOP} — никаких неожиданных значений.
4. При stop_triggered → stop_hits непустой; при warning → warning_hits непустой.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from core.domain import AlertStage
from core.rules.evaluator import evaluate_stop_rules
from core.rules.types import RuleContext
from core.scanner.models import ScannedAdRow

# Стратегии для разумных значений метрик
_pos_decimal = st.decimals(
    min_value=Decimal("0"),
    max_value=Decimal("10000"),
    allow_nan=False,
    allow_infinity=False,
    places=2,
)
_pos_int = st.integers(min_value=0, max_value=10000)
_cpa = st.decimals(
    min_value=Decimal("1"),
    max_value=Decimal("500"),
    allow_nan=False,
    allow_infinity=False,
    places=2,
)
_warn_pct = st.decimals(
    min_value=Decimal("50"),
    max_value=Decimal("99"),
    allow_nan=False,
    allow_infinity=False,
    places=0,
)


def _make_ctx(cpa: Decimal, warn_pct: Decimal) -> RuleContext:
    """Создаёт RuleContext с разумными значениями."""
    return RuleContext(
        cpa_amount=cpa,
        warning_percent_of_stop=warn_pct,
        stop_percent_of_base=Decimal("80"),
        frequency_anomaly_enabled=False,  # отключаем frequency — слишком много параметров
    )


def _make_row(
    fb_ad_id: str = "111",
    spend: Decimal = Decimal("0"),
    cpc: Decimal | None = None,
    leads: int = 0,
    registrations: int = 0,
    deposits: int = 0,
    cost_per_lead: Decimal | None = None,
    cost_per_registration: Decimal | None = None,
) -> ScannedAdRow:
    """Создаёт ScannedAdRow для тестов."""
    return ScannedAdRow(
        fb_ad_id=fb_ad_id,
        campaign_name="TEST | 111",
        adset_name="TEST_ADSET",
        ad_name="TEST_AD",
        delivery_status="ACTIVE",
        spend=spend,
        budget="$100",
        reach=1000,
        impressions=2000,
        clicks=50,
        cpc=cpc,
        leads=leads,
        registrations=registrations,
        deposits=deposits,
        cost_per_lead=cost_per_lead,
        cost_per_registration=cost_per_registration,
    )


# Property 1: при spend=0 и нулевых событиях — никаких алертов.
# Примечание: regs_no_dep_stop и funnel_ladder срабатывают по количеству событий
# (registrations/deposits), независимо от spend. Чтобы изолировать spend-зависимые
# правила, используем leads=registrations=deposits=0.
@given(
    cpa=_cpa,
    warn_pct=_warn_pct,
)
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_zero_spend_zero_events_never_triggers(
    cpa: Decimal,
    warn_pct: Decimal,
) -> None:
    """При spend=0 и нулевых событиях ни одно правило не может сработать."""
    row = _make_row(spend=Decimal("0"), leads=0, registrations=0, deposits=0)
    ctx = _make_ctx(cpa=cpa, warn_pct=warn_pct)
    result = evaluate_stop_rules(row, ctx)
    assert result.stage is None, (
        f"spend=0 + нет событий не должен триггерить, но stage={result.stage}, "
        f"warning_hits={result.warning_hits}, stop_hits={result.stop_hits}"
    )


# Property 2: детерминизм — одинаковый input → одинаковый output.
@given(
    cpa=_cpa,
    warn_pct=_warn_pct,
    spend=_pos_decimal,
    leads=_pos_int,
    registrations=_pos_int,
    deposits=_pos_int,
)
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_evaluate_is_deterministic(
    cpa: Decimal,
    warn_pct: Decimal,
    spend: Decimal,
    leads: int,
    registrations: int,
    deposits: int,
) -> None:
    """evaluate_stop_rules детерминирована: два вызова с одним input → одинаковый output."""
    row = _make_row(spend=spend, leads=leads, registrations=registrations, deposits=deposits)
    ctx = _make_ctx(cpa=cpa, warn_pct=warn_pct)
    result1 = evaluate_stop_rules(row, ctx)
    result2 = evaluate_stop_rules(row, ctx)
    assert result1.stage == result2.stage
    assert result1.warning_hits == result2.warning_hits
    assert result1.stop_hits == result2.stop_hits


# Property 3: stage всегда из допустимого множества.
@given(
    cpa=_cpa,
    warn_pct=_warn_pct,
    spend=_pos_decimal,
    leads=_pos_int,
    registrations=_pos_int,
    deposits=_pos_int,
)
@settings(max_examples=150, suppress_health_check=[HealthCheck.too_slow])
def test_stage_is_valid_enum_value(
    cpa: Decimal,
    warn_pct: Decimal,
    spend: Decimal,
    leads: int,
    registrations: int,
    deposits: int,
) -> None:
    """stage результата всегда None, WARNING или STOP — не может быть произвольной строкой."""
    row = _make_row(spend=spend, leads=leads, registrations=registrations, deposits=deposits)
    ctx = _make_ctx(cpa=cpa, warn_pct=warn_pct)
    result = evaluate_stop_rules(row, ctx)
    assert result.stage in (None, AlertStage.WARNING, AlertStage.STOP), (
        f"stage={result.stage!r} не из допустимого множества {{None, WARNING, STOP}}"
    )


# Property 4: консистентность stage и hits.
@given(
    cpa=_cpa,
    warn_pct=_warn_pct,
    spend=_pos_decimal,
    leads=_pos_int,
    registrations=_pos_int,
    deposits=_pos_int,
)
@settings(max_examples=150, suppress_health_check=[HealthCheck.too_slow])
def test_stage_consistent_with_hits(
    cpa: Decimal,
    warn_pct: Decimal,
    spend: Decimal,
    leads: int,
    registrations: int,
    deposits: int,
) -> None:
    """Если stage=STOP → stop_hits непустой; stage=WARNING → warning_hits непустой;
    stage=None → оба пустые. Не должно быть stage=STOP при пустых stop_hits."""
    row = _make_row(spend=spend, leads=leads, registrations=registrations, deposits=deposits)
    ctx = _make_ctx(cpa=cpa, warn_pct=warn_pct)
    result = evaluate_stop_rules(row, ctx)

    if result.stage == AlertStage.STOP:
        assert len(result.stop_hits) > 0, "stage=STOP но stop_hits пустой — нарушение инварианта"
    elif result.stage == AlertStage.WARNING:
        assert len(result.warning_hits) > 0, (
            "stage=WARNING но warning_hits пустой — нарушение инварианта"
        )
    elif result.stage is None:
        assert len(result.stop_hits) == 0 and len(result.warning_hits) == 0, (
            "stage=None но hits непустые — нарушение инварианта"
        )

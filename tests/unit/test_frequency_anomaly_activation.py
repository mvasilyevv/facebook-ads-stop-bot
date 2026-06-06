# -*- coding: utf-8 -*-
"""Unit: активация frequency-anomaly (правило 7, #37).

Проверяем проводку build_rule_context (opt-in per-offer через frequency_threshold)
и срабатывание правила через evaluate_stop_rules + sanity-фильтры против ложных
стопов на старте. Money-критично: правило молчит, пока порог не задан у оффера.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from core.domain import AlertStage
from core.observer.pipeline import build_rule_context
from core.observer.queries import OfferRules
from core.rules.evaluator import evaluate_stop_rules
from core.scanner.models import ScannedAdRow


def _offer(*, frequency_threshold: Decimal | None, cpa: Decimal = Decimal("3")) -> OfferRules:
    return OfferRules(
        offer_id=uuid.uuid4(),
        code="TST",
        name="test offer",
        spend_no_event_threshold=None,
        cpa_threshold=cpa,
        cpm_threshold=None,
        ctr_threshold=None,
        frequency_threshold=frequency_threshold,
        funnel_ratio_threshold=None,
    )


def _row(
    *,
    frequency: Decimal | None,
    impressions: int = 2000,
    reach: int = 400,
    spend: Decimal = Decimal("0"),
) -> ScannedAdRow:
    """Row с пустой воронкой (spend=0, нет событий) — изолирует frequency-правило."""
    return ScannedAdRow(
        fb_ad_id="111",
        campaign_name="TST | 111",
        adset_name="ADSET",
        ad_name="AD",
        delivery_status="ACTIVE",
        spend=spend,
        reach=reach,
        impressions=impressions,
        clicks=0,
        leads=0,
        registrations=0,
        deposits=0,
        frequency=frequency,
    )


# ====================== build_rule_context: проводка opt-in ======================


# Оффер без frequency_threshold → frequency-правило выключено. M1: impressions/reach
# теперь заполняются ВСЕГДА (нужны guardrail'у как sanity-минимум показов), но
# frequency_anomaly остаётся off и frequency_current не протекает.
def test_context_no_threshold_disables_rule() -> None:
    ctx = build_rule_context(
        _offer(frequency_threshold=None),
        frequency_current=Decimal("5.0"),
        impressions=2000,
        reach=400,
    )
    assert ctx.frequency_anomaly_enabled is False
    assert ctx.frequency_current is None
    assert ctx.impressions == 2000
    assert ctx.reach == 400


# Оффер с порогом → enabled, stop=порог, warning=свёртка 80%, данные проброшены.
def test_context_with_threshold_enables_and_folds_warning() -> None:
    ctx = build_rule_context(
        _offer(frequency_threshold=Decimal("4.0")),
        frequency_current=Decimal("3.7"),
        impressions=2000,
        reach=400,
    )
    assert ctx.frequency_anomaly_enabled is True
    assert ctx.frequency_stop_threshold == Decimal("4.0")
    assert ctx.frequency_warning_threshold == Decimal("3.20")  # 4.0 * 80%
    assert ctx.frequency_current == Decimal("3.7")
    assert ctx.impressions == 2000
    assert ctx.reach == 400


# frequency_threshold = 0 трактуется как выключено (как None).
def test_context_zero_threshold_disabled() -> None:
    ctx = build_rule_context(
        _offer(frequency_threshold=Decimal("0")),
        frequency_current=Decimal("5.0"),
        impressions=2000,
        reach=400,
    )
    assert ctx.frequency_anomaly_enabled is False


# ====================== evaluate_stop_rules: срабатывание ======================


# Частота выше stop-порога + достаточно данных → STOP с кодом frequency_anomaly.
def test_high_frequency_triggers_stop() -> None:
    offer = _offer(frequency_threshold=Decimal("4.0"))
    row = _row(frequency=Decimal("5.0"), impressions=2000, reach=400)
    ctx = build_rule_context(
        offer, frequency_current=row.frequency, impressions=row.impressions, reach=row.reach
    )
    result = evaluate_stop_rules(row, ctx)
    assert result.stage == AlertStage.STOP
    assert "frequency_anomaly" in result.stop_rule_codes


# Частота между warning(3.2) и stop(4.0), истории нет → WARNING.
def test_mid_frequency_triggers_warning() -> None:
    offer = _offer(frequency_threshold=Decimal("4.0"))
    row = _row(frequency=Decimal("3.5"), impressions=2000, reach=550)
    ctx = build_rule_context(
        offer, frequency_current=row.frequency, impressions=row.impressions, reach=row.reach
    )
    result = evaluate_stop_rules(row, ctx)
    assert result.stage == AlertStage.WARNING
    assert "frequency_anomaly" in result.warning_rule_codes


# Частота ниже warning-порога → правило молчит (нет hit).
def test_low_frequency_no_hit() -> None:
    offer = _offer(frequency_threshold=Decimal("4.0"))
    row = _row(frequency=Decimal("2.0"), impressions=2000, reach=1000)
    ctx = build_rule_context(
        offer, frequency_current=row.frequency, impressions=row.impressions, reach=row.reach
    )
    result = evaluate_stop_rules(row, ctx)
    assert result.stage is None


# ====================== sanity-фильтры (защита от ложных стопов) ======================


# Высокая частота, но reach < min_reach → НЕ стопаем (ненадёжная выборка на старте).
def test_low_reach_suppresses_stop() -> None:
    offer = _offer(frequency_threshold=Decimal("4.0"))
    row = _row(frequency=Decimal("5.0"), impressions=2000, reach=50)  # reach < 100
    ctx = build_rule_context(
        offer, frequency_current=row.frequency, impressions=row.impressions, reach=row.reach
    )
    result = evaluate_stop_rules(row, ctx)
    assert result.stage is None


# Высокая частота, но impressions < min_impressions → НЕ стопаем (мало данных).
def test_low_impressions_suppresses_stop() -> None:
    offer = _offer(frequency_threshold=Decimal("4.0"))
    row = _row(frequency=Decimal("5.0"), impressions=300, reach=150)  # impr < 500
    ctx = build_rule_context(
        offer, frequency_current=row.frequency, impressions=row.impressions, reach=row.reach
    )
    result = evaluate_stop_rules(row, ctx)
    assert result.stage is None


# Частота выше outlier_cap (10) → выброс на старте (малый reach), НЕ стопаем.
def test_outlier_frequency_suppressed() -> None:
    offer = _offer(frequency_threshold=Decimal("4.0"))
    row = _row(frequency=Decimal("45.0"), impressions=2000, reach=400)
    ctx = build_rule_context(
        offer, frequency_current=row.frequency, impressions=row.impressions, reach=row.reach
    )
    result = evaluate_stop_rules(row, ctx)
    assert result.stage is None


# Оффер без порога: даже при экстремальной частоте frequency-правило не стопает.
def test_disabled_offer_never_stops_on_frequency() -> None:
    offer = _offer(frequency_threshold=None)
    row = _row(frequency=Decimal("8.0"), impressions=5000, reach=600)
    ctx = build_rule_context(
        offer, frequency_current=row.frequency, impressions=row.impressions, reach=row.reach
    )
    result = evaluate_stop_rules(row, ctx)
    assert result.stage is None

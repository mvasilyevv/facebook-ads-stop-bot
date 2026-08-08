# -*- coding: utf-8 -*-
"""Unit-тесты pure-функций core/enable_reco/analyzer.py."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.enable_reco.analyzer import (
    AnalyzerThresholds,
    MetricSnapshot,
    OfferThresholds,
)
from core.enable_reco.analyzer import (
    should_recommend as _should_recommend,
)


def should_recommend(**kwargs):
    return _should_recommend(
        account_currency="USD",
        currency_exponent=2,
        **kwargs,
    )


def _now() -> datetime:
    return datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)


def _confirmed_offer(cpa: Decimal) -> OfferThresholds:
    return OfferThresholds(
        cpa_threshold=cpa,
        currency="USD",
        stop_percent_of_rule=Decimal("80"),
        warning_percent_of_stop=Decimal("80"),
    )


def _metric(
    *,
    minutes_ago: int = 5,
    spend: str | None = "1.0",
    cost_per_lead: str | None = None,
    cost_per_registration: str | None = None,
    registrations: int | None = 0,
    deposits: int | None = 0,
    leads: int | None = 0,
    clicks: int | None = 0,
    cpc: str | None = None,
) -> MetricSnapshot:
    return MetricSnapshot(
        cycle_ts=_now() - timedelta(minutes=minutes_ago),
        spend=Decimal(spend) if spend is not None else None,
        cost_per_lead=Decimal(cost_per_lead) if cost_per_lead is not None else None,
        cost_per_registration=Decimal(cost_per_registration)
        if cost_per_registration is not None
        else None,
        registrations=registrations,
        deposits=deposits,
        leads=leads,
        clicks=clicks,
        cpc=Decimal(cpc) if cpc is not None else None,
    )


# Сценарий: state='normal' — рекомендовать нельзя (не наша зона ответственности)
def test_skips_non_recommendable_state() -> None:
    decision = should_recommend(
        alert_state="normal",
        snoozed_until=None,
        now=_now(),
        metrics=[_metric()],
        offer=_confirmed_offer(Decimal("10")),
    )
    assert decision.recommend is False
    assert "state=normal" in (decision.skip_reason or "")


# Сценарий: snooze ещё действует → пропускаем
def test_skips_when_snoozed() -> None:
    decision = should_recommend(
        alert_state="stop_sent",
        snoozed_until=_now() + timedelta(hours=1),
        now=_now(),
        metrics=[_metric()],
        offer=_confirmed_offer(Decimal("10")),
    )
    assert decision.recommend is False
    assert decision.skip_reason == "snoozed"


# Сценарий: метрик нет — данных мало, не рекомендуем
def test_skips_when_no_metrics() -> None:
    decision = should_recommend(
        alert_state="stop_sent",
        snoozed_until=None,
        now=_now(),
        metrics=[],
        offer=_confirmed_offer(Decimal("10")),
    )
    assert decision.recommend is False
    assert "мало метрик" in (decision.skip_reason or "")


# Канонический evaluator не видит ни STOP, ни WARNING → level=ok.
def test_recommends_ok_when_canonical_rules_are_clear() -> None:
    decision = should_recommend(
        alert_state="stop_sent",
        snoozed_until=None,
        now=_now(),
        metrics=[
            _metric(
                spend="2.0",
                clicks=20,
                cpc="0.10",
                leads=2,
                cost_per_lead="1.0",
                registrations=1,
                cost_per_registration="1.0",
            )
        ],
        offer=_confirmed_offer(Decimal("10")),
    )
    assert decision.recommend is True
    assert decision.level == "ok"
    assert "Канонические" in decision.reasons[0]


# Канонический evaluator возвращает WARNING → рекомендация остаётся ручной.
def test_recommends_warning_when_canonical_rule_is_warning() -> None:
    decision = should_recommend(
        alert_state="stop_sent",
        snoozed_until=None,
        now=_now(),
        metrics=[
            _metric(
                spend="1.0",
                clicks=20,
                cpc="0.05",
                leads=2,
                cost_per_lead="0.5",
                registrations=1,
                cost_per_registration="1.40",
            )
        ],
        offer=_confirmed_offer(Decimal("10")),
    )
    assert decision.recommend is True
    assert decision.level == "warning"
    assert len(decision.reasons) == 1


# Сценарий: spend огромный, cost_per_lead дорогой, депов нет — ни одного условия → не рекомендуем
def test_no_recommendation_when_metrics_still_bad() -> None:
    decision = should_recommend(
        alert_state="stop_sent",
        snoozed_until=None,
        now=_now(),
        metrics=[_metric(spend="50.0", cost_per_lead="40.0", deposits=0)],
        offer=_confirmed_offer(Decimal("10")),
    )
    assert decision.recommend is False
    assert "Стоп" in (decision.skip_reason or "") or "границ" in (decision.skip_reason or "")


# Сценарий: state='disabled' — тоже допустим (юзер хотел потом включить вручную)
def test_disabled_state_is_recommendable() -> None:
    decision = should_recommend(
        alert_state="disabled",
        snoozed_until=None,
        now=_now(),
        metrics=[
            _metric(
                spend="1.0",
                clicks=20,
                cpc="0.05",
                leads=2,
                cost_per_lead="0.5",
                registrations=1,
                cost_per_registration="1.0",
            )
        ],
        offer=_confirmed_offer(Decimal("10")),
    )
    assert decision.recommend is True


# Deposit без registration не подтверждает воронку и не разрешает enable.
def test_deposits_without_registration_are_not_signal() -> None:
    decision = should_recommend(
        alert_state="stop_sent",
        snoozed_until=None,
        now=_now(),
        metrics=[
            _metric(
                spend="9999.0",
                cost_per_lead="9999.0",
                registrations=0,
                deposits=3,
            )
        ],
        offer=_confirmed_offer(Decimal("10")),
    )
    assert decision.recommend is False


# Даже подтверждённая воронка не обходит действующий spend/CPA stop.
def test_registration_and_deposit_do_not_override_canonical_stop() -> None:
    decision = should_recommend(
        alert_state="stop_sent",
        snoozed_until=None,
        now=_now(),
        metrics=[
            _metric(
                spend="9999.0",
                cost_per_lead="9999.0",
                registrations=1,
                deposits=3,
            )
        ],
        offer=_confirmed_offer(Decimal("10")),
    )
    assert decision.recommend is False


# Без CPA канонический evaluator не создаёт ложную уверенность даже при воронке.
def test_no_cpa_threshold_never_auto_recommends() -> None:
    decision_no_deposits = should_recommend(
        alert_state="stop_sent",
        snoozed_until=None,
        now=_now(),
        metrics=[_metric(spend="100.0", deposits=0)],
        offer=OfferThresholds(cpa_threshold=None),
    )
    assert decision_no_deposits.recommend is False

    decision_with_unconfirmed_deposits = should_recommend(
        alert_state="stop_sent",
        snoozed_until=None,
        now=_now(),
        metrics=[_metric(spend="100.0", registrations=0, deposits=5)],
        offer=OfferThresholds(cpa_threshold=None),
    )
    assert decision_with_unconfirmed_deposits.recommend is False

    decision_with_funnel = should_recommend(
        alert_state="stop_sent",
        snoozed_until=None,
        now=_now(),
        metrics=[_metric(spend="100.0", registrations=1, deposits=5)],
        offer=OfferThresholds(cpa_threshold=None),
    )
    assert decision_with_funnel.recommend is False


# Сценарий: min_metrics_required=3 в thresholds — две метрики не хватит
def test_custom_threshold_min_metrics() -> None:
    decision = should_recommend(
        alert_state="stop_sent",
        snoozed_until=None,
        now=_now(),
        metrics=[_metric(spend="1.0", cost_per_lead="5.0")],
        offer=_confirmed_offer(Decimal("10")),
        thresholds=AnalyzerThresholds(min_metrics_required=3),
    )
    assert decision.recommend is False
    assert "мало метрик" in (decision.skip_reason or "")


# Сценарий: snapshot содержит сводку — пригодится для записи в БД.
# spend = ПОСЛЕДНИЙ кумулятивный снимок (не сумма): ad_metrics.spend нарастающий,
# два снимка по 0.5 → итог 0.5 (свежий), не 1.0.
def test_snapshot_summary_keys() -> None:
    decision = should_recommend(
        alert_state="stop_sent",
        snoozed_until=None,
        now=_now(),
        metrics=[
            _metric(
                spend="0.5",
                clicks=20,
                cpc="0.02",
                leads=2,
                cost_per_lead="0.25",
                registrations=1,
                cost_per_registration="0.5",
            ),
            _metric(
                minutes_ago=15,
                spend="0.5",
                clicks=20,
                cpc="0.02",
                leads=2,
                registrations=1,
                cost_per_registration="0.5",
            ),
        ],
        offer=_confirmed_offer(Decimal("10")),
    )
    assert decision.recommend is True
    snap = decision.snapshot
    assert snap["metrics_count"] == 2
    # spend = последний снимок (0.5), не сумма снимков (1.0)
    assert snap["total_spend"] == "0.5"
    assert "latest_cycle_ts" in snap


# Сценарий R2 (CRIT-2): spend в ad_metrics кумулятивный (нарастающий с начала
# cabinet-дня). После паузы рекламы все снимки в окне держат одно значение S.
# Snapshot и canonical evaluation должны использовать последний снимок, не сумму.
def test_cumulative_spend_uses_latest_not_sum() -> None:
    # 12 одинаковых кумулятивных снимков по 3.0 (реклама на паузе → spend плоский)
    metrics = [
        _metric(
            minutes_ago=15 * i,
            spend="3.0",
            clicks=30,
            cpc="0.10",
            leads=3,
            cost_per_lead="1.0",
            registrations=2,
            cost_per_registration="1.0",
        )
        for i in range(12)
    ]
    decision = should_recommend(
        alert_state="stop_sent",
        snoozed_until=None,
        now=_now(),
        metrics=metrics,
        offer=_confirmed_offer(Decimal("10")),
    )
    assert decision.recommend is True
    assert decision.level == "ok"
    # snapshot тоже отражает последний снимок, не сумму
    assert decision.snapshot["total_spend"] == "3.0"

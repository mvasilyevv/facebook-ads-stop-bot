# -*- coding: utf-8 -*-
"""Unit-тесты кейса куратора: сигнал «мало показов + хороший CTR» + grace-механика."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.enable_reco.analyzer import (
    MetricSnapshot,
    OfferThresholds,
)
from core.enable_reco.analyzer import (
    should_recommend as _should_recommend,
)
from core.observer.enable_grace import (
    EnableGrace as _EnableGrace,
)
from core.observer.enable_grace import (
    grace_is_active as _grace_is_active,
)

_NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
_DAY_START = _NOW.replace(hour=0, minute=0, second=0, microsecond=0)


def should_recommend(**kwargs):
    return _should_recommend(
        account_currency="USD",
        currency_exponent=2,
        **kwargs,
    )


def EnableGrace(**kwargs):
    return _EnableGrace(
        currency="USD",
        currency_exponent=2,
        **kwargs,
    )


def grace_is_active(grace, **kwargs):
    return _grace_is_active(
        grace,
        currency="USD",
        currency_exponent=2,
        **kwargs,
    )


def _confirmed_offer(cpa: Decimal) -> OfferThresholds:
    return OfferThresholds(
        cpa_threshold=cpa,
        currency="USD",
        stop_percent_of_rule=Decimal("80"),
        warning_percent_of_stop=Decimal("80"),
    )


def _snap(
    *,
    impressions: int | None,
    ctr: str | None,
    spend: str = "0.44",
    minutes_ago: int = 5,
) -> MetricSnapshot:
    return MetricSnapshot(
        cycle_ts=_NOW - timedelta(minutes=minutes_ago),
        spend=Decimal(spend),
        impressions=impressions,
        ctr=Decimal(ctr) if ctr is not None else None,
    )


def _decide(metrics: list[MetricSnapshot]):
    return should_recommend(
        alert_state="disabled",
        snoozed_until=None,
        now=_NOW,
        metrics=metrics,
        offer=_confirmed_offer(Decimal("3.00")),
    )


# Кейс куратора (сценарий с фото): 108 показов, CTR 3.7% → включить и держать до CPL
def test_curator_low_impressions_good_ctr_holds() -> None:
    decision = _decide([_snap(impressions=108, ctr="3.7")])
    assert decision.recommend is True
    assert decision.hold_until_cpl is True
    assert decision.level == "warning"
    assert decision.snapshot["hold_until_cpl"] is True
    # Спенд-кап = 1×CPA оффера — до него держим стоп-правила
    assert decision.snapshot["grace_spend_cap"] == "3.00"
    assert "показов мало" in decision.reasons[0]


# Без CPA curator не имеет права выдумывать денежный кап или рекомендовать enable.
def test_curator_without_cpa_fails_closed() -> None:
    decision = should_recommend(
        alert_state="disabled",
        snoozed_until=None,
        now=_NOW,
        metrics=[_snap(impressions=108, ctr="3.7")],
        offer=OfferThresholds(cpa_threshold=None),
    )
    assert decision.recommend is False
    assert decision.hold_until_cpl is False
    assert "CPA" in (decision.skip_reason or "")
    assert "grace_spend_cap" not in decision.snapshot


# Мало показов, но CTR плохой — кейс куратора НЕ срабатывает
def test_curator_low_ctr_not_recommended() -> None:
    decision = _decide([_snap(impressions=108, ctr="0.5")])
    assert decision.hold_until_cpl is False


# Показов много — данных достаточно, кейс куратора не применяется (CTR любой)
def test_curator_enough_impressions_skipped() -> None:
    decision = _decide([_snap(impressions=5000, ctr="4.0")])
    assert decision.hold_until_cpl is False


# Recovery-сигналы не смешиваются с curator: hold-рекомендация всегда level=warning
def test_curator_not_mixed_with_recovery_level() -> None:
    # spend мал (recovery-правило 1 тоже сработало бы) — но curator-ветка раньше
    decision = _decide([_snap(impressions=50, ctr="10.0", spend="0.05")])
    assert decision.hold_until_cpl is True
    assert decision.level == "warning"


# Worker может запретить curator-ветку для небезопасного кандидата,
# не ломая обычные recovery-правила.
def test_curator_branch_can_be_disabled_for_unsafe_candidate() -> None:
    decision = should_recommend(
        alert_state="disabled",
        snoozed_until=None,
        now=_NOW,
        metrics=[_snap(impressions=108, ctr="3.7", spend="50.00")],
        offer=_confirmed_offer(Decimal("3.00")),
        allow_curator=False,
    )
    assert decision.recommend is False
    assert decision.hold_until_cpl is False


# grace_is_active: истёкшее время → False, даже если спенд мал
def test_grace_expired_by_time() -> None:
    grace = EnableGrace(
        until=_NOW - timedelta(seconds=1),
        spend_cap=Decimal("3"),
        baseline_spend=Decimal("0.5"),
        cabinet_day_start=_DAY_START,
    )
    assert (
        grace_is_active(
            grace,
            now=_NOW,
            spend=Decimal("1"),
            cabinet_day_start=_DAY_START,
        )
        is False
    )


# grace_is_active: спенд-кап достигнут → False, дальше судит цена лида
def test_grace_expired_by_spend_cap() -> None:
    grace = EnableGrace(
        until=_NOW + timedelta(hours=1),
        spend_cap=Decimal("3"),
        baseline_spend=Decimal("0.5"),
        cabinet_day_start=_DAY_START,
    )
    assert (
        grace_is_active(
            grace,
            now=_NOW,
            spend=Decimal("3.00"),
            cabinet_day_start=_DAY_START,
        )
        is False
    )


# grace_is_active: время не вышло и спенд ниже капа → True
def test_grace_active() -> None:
    grace = EnableGrace(
        until=_NOW + timedelta(hours=1),
        spend_cap=Decimal("3"),
        baseline_spend=Decimal("0.5"),
        cabinet_day_start=_DAY_START,
    )
    assert (
        grace_is_active(
            grace,
            now=_NOW,
            spend=Decimal("1.50"),
            cabinet_day_start=_DAY_START,
        )
        is True
    )


def test_grace_missing_spend_fails_closed() -> None:
    grace = EnableGrace(
        until=_NOW + timedelta(hours=1),
        spend_cap=Decimal("3"),
        baseline_spend=Decimal("0.5"),
        cabinet_day_start=_DAY_START,
    )
    assert (
        grace_is_active(
            grace,
            now=_NOW,
            spend=None,
            cabinet_day_start=_DAY_START,
        )
        is False
    )


def test_grace_cumulative_reset_fails_closed() -> None:
    grace = EnableGrace(
        until=_NOW + timedelta(hours=1),
        spend_cap=Decimal("3"),
        baseline_spend=Decimal("0.5"),
        cabinet_day_start=_DAY_START,
    )
    assert (
        grace_is_active(
            grace,
            now=_NOW,
            spend=Decimal("0.49"),
            cabinet_day_start=_DAY_START,
        )
        is False
    )


def test_grace_from_previous_cabinet_day_fails_closed() -> None:
    grace = EnableGrace(
        until=_NOW + timedelta(hours=1),
        spend_cap=Decimal("3"),
        baseline_spend=Decimal("0.5"),
        cabinet_day_start=_DAY_START - timedelta(days=1),
    )
    assert (
        grace_is_active(
            grace,
            now=_NOW,
            spend=Decimal("1"),
            cabinet_day_start=_DAY_START,
        )
        is False
    )

from __future__ import annotations

from decimal import Decimal

from core.domain import DeliveryStatus
from core.rules import CleanScanState, MetricsSnapshot, build_threshold_pack, evaluate_resume


# Проверяет, что объявление не включается обратно, пока не накопилось два чистых скана подряд.
def test_resume_requires_two_clean_scans() -> None:
    thresholds = build_threshold_pack(Decimal("5.00"))
    snapshot = MetricsSnapshot(
        spend=Decimal("0.20"),
        leads=1,
        cost_per_lead=Decimal("0.20"),
        registrations=1,
        cost_per_registration=Decimal("0.20"),
        deposits=0,
        cpc=Decimal("0.05"),
    )

    decision = evaluate_resume(
        snapshot=snapshot,
        thresholds=thresholds,
        clean_scans=CleanScanState(streak=1),
        delivery_status=DeliveryStatus.PAUSED,
        is_blocked=False,
    )

    assert decision.should_resume is False
    assert decision.reason == "Недостаточно чистых сканов подряд для безопасного включения"


# Проверяет, что объявление держится на паузе, если расход уже превысил порог клика, а кликов еще нет.
def test_resume_blocks_when_click_stage_is_not_viable() -> None:
    thresholds = build_threshold_pack(Decimal("5.00"))
    snapshot = MetricsSnapshot(
        spend=Decimal("0.16"),
        clicks=0,
        cpc=None,
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
    )

    decision = evaluate_resume(
        snapshot=snapshot,
        thresholds=thresholds,
        clean_scans=CleanScanState(streak=2),
        delivery_status=DeliveryStatus.PAUSED,
        is_blocked=False,
    )

    assert decision.should_resume is False
    assert decision.reason == "Расход уже превысил порог клика без самого клика"


# Проверяет, что объявление держится на паузе, если расход уже превысил порог лида, а лидов еще нет.
def test_resume_blocks_when_lead_stage_is_not_viable() -> None:
    thresholds = build_threshold_pack(Decimal("5.00"))
    snapshot = MetricsSnapshot(
        spend=Decimal("0.51"),
        clicks=5,
        cpc=Decimal("0.06"),
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
    )

    decision = evaluate_resume(
        snapshot=snapshot,
        thresholds=thresholds,
        clean_scans=CleanScanState(streak=2),
        delivery_status=DeliveryStatus.PAUSED,
        is_blocked=False,
    )

    assert decision.should_resume is False
    assert decision.reason == "Расход уже превысил порог лида без самого лида"


# Проверяет, что долетевшие лиды и регистрации делают объявление пригодным для повторного запуска.
def test_resume_allows_ad_when_metrics_return_to_safe_zone() -> None:
    thresholds = build_threshold_pack(Decimal("5.00"))
    snapshot = MetricsSnapshot(
        spend=Decimal("0.51"),
        clicks=6,
        cpc=Decimal("0.06"),
        leads=2,
        cost_per_lead=Decimal("0.25"),
        registrations=1,
        cost_per_registration=Decimal("0.25"),
        deposits=0,
    )

    decision = evaluate_resume(
        snapshot=snapshot,
        thresholds=thresholds,
        clean_scans=CleanScanState(streak=2),
        delivery_status=DeliveryStatus.PAUSED,
        is_blocked=False,
    )

    assert decision.should_resume is True
    assert decision.reason == "Объявление снова безопасно для запуска"


# Проверяет, что объявления со статусом «не показывается» никогда не включаются автоматически.
def test_resume_blocks_not_delivering_ads() -> None:
    thresholds = build_threshold_pack(Decimal("5.00"))
    snapshot = MetricsSnapshot(
        spend=Decimal("0.10"),
        leads=1,
        cost_per_lead=Decimal("0.10"),
        registrations=1,
        cost_per_registration=Decimal("0.10"),
        deposits=0,
        cpc=Decimal("0.02"),
    )

    decision = evaluate_resume(
        snapshot=snapshot,
        thresholds=thresholds,
        clean_scans=CleanScanState(streak=2),
        delivery_status=DeliveryStatus.NOT_DELIVERING,
        is_blocked=False,
    )

    assert decision.should_resume is False
    assert decision.reason == "Объявление не показывается и требует ручной проверки"

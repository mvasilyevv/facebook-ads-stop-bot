from __future__ import annotations

from decimal import Decimal

from core.rules import MetricsSnapshot, build_threshold_pack, evaluate_pause_reasons

_THRESHOLDS = build_threshold_pack(Decimal("5.00"))


# Проверяет, что без нарушений объявление не получает причин для остановки.
def test_no_reasons_when_all_metrics_within_bounds() -> None:
    snapshot = MetricsSnapshot(
        spend=Decimal("0.50"),
        clicks=10,
        cpc=Decimal("0.05"),
        leads=2,
        cost_per_lead=Decimal("0.25"),
        registrations=1,
        cost_per_registration=Decimal("0.50"),
        deposits=0,
    )
    assert evaluate_pause_reasons(snapshot, _THRESHOLDS) == []


# Проверяет, что отсутствие всех числовых метрик не вызывает ложных срабатываний.
def test_no_reasons_when_metrics_are_none() -> None:
    snapshot = MetricsSnapshot(spend=Decimal("0.00"))
    assert evaluate_pause_reasons(snapshot, _THRESHOLDS) == []


# Правило 1: клик превысил допустимую долю CPA.
def test_cpc_exceeds_threshold() -> None:
    snapshot = MetricsSnapshot(
        spend=Decimal("0.50"),
        clicks=3,
        cpc=Decimal("0.11"),
    )
    reasons = evaluate_pause_reasons(snapshot, _THRESHOLDS)
    assert "Клик превысил допустимую долю CPA" in reasons


# Правило 1: клик ровно на пороге не должен срабатывать.
def test_cpc_at_exact_threshold_does_not_trigger() -> None:
    snapshot = MetricsSnapshot(
        spend=Decimal("0.50"),
        clicks=5,
        cpc=Decimal("0.10"),
    )
    reasons = evaluate_pause_reasons(snapshot, _THRESHOLDS)
    assert "Клик превысил допустимую долю CPA" not in reasons


# Правило 2: лид превысил допустимую долю CPA.
def test_cpl_exceeds_threshold() -> None:
    snapshot = MetricsSnapshot(
        spend=Decimal("1.00"),
        leads=1,
        cost_per_lead=Decimal("0.51"),
    )
    reasons = evaluate_pause_reasons(snapshot, _THRESHOLDS)
    assert "Лид превысил допустимую долю CPA" in reasons


# Правило 2: лид ровно на пороге не должен срабатывать.
def test_cpl_at_exact_threshold_does_not_trigger() -> None:
    snapshot = MetricsSnapshot(
        spend=Decimal("0.50"),
        leads=1,
        cost_per_lead=Decimal("0.50"),
    )
    reasons = evaluate_pause_reasons(snapshot, _THRESHOLDS)
    assert "Лид превысил допустимую долю CPA" not in reasons


# Правило 3: регистрация превысила допустимую долю CPA.
def test_registration_cost_exceeds_threshold() -> None:
    snapshot = MetricsSnapshot(
        spend=Decimal("2.00"),
        registrations=1,
        cost_per_registration=Decimal("1.01"),
        deposits=0,
    )
    reasons = evaluate_pause_reasons(snapshot, _THRESHOLDS)
    assert "Регистрация превысила допустимую долю CPA" in reasons


# Правило 3: регистрация ровно на пороге не должна срабатывать.
def test_registration_cost_at_exact_threshold_does_not_trigger() -> None:
    snapshot = MetricsSnapshot(
        spend=Decimal("1.00"),
        registrations=1,
        cost_per_registration=Decimal("1.00"),
        deposits=0,
    )
    reasons = evaluate_pause_reasons(snapshot, _THRESHOLDS)
    assert "Регистрация превысила допустимую долю CPA" not in reasons


# Правило 4: пять регистраций без депозитов.
def test_five_registrations_without_deposits() -> None:
    snapshot = MetricsSnapshot(
        spend=Decimal("2.00"),
        registrations=5,
        cost_per_registration=Decimal("0.40"),
        deposits=0,
    )
    reasons = evaluate_pause_reasons(snapshot, _THRESHOLDS)
    assert "Набралось пять регистраций без депозитов" in reasons


# Правило 4: четыре регистрации ещё допустимы.
def test_four_registrations_without_deposits_is_safe() -> None:
    snapshot = MetricsSnapshot(
        spend=Decimal("2.00"),
        registrations=4,
        cost_per_registration=Decimal("0.50"),
        deposits=0,
    )
    reasons = evaluate_pause_reasons(snapshot, _THRESHOLDS)
    assert "Набралось пять регистраций без депозитов" not in reasons


# Правило 4: пять регистраций с хотя бы одним депозитом — правило не срабатывает.
def test_five_registrations_with_deposit_is_safe() -> None:
    snapshot = MetricsSnapshot(
        spend=Decimal("3.00"),
        registrations=5,
        cost_per_registration=Decimal("0.60"),
        deposits=1,
    )
    reasons = evaluate_pause_reasons(snapshot, _THRESHOLDS)
    assert "Набралось пять регистраций без депозитов" not in reasons


# Правило 5: расход достиг лимита без депозитов при нормальной стоимости регистрации.
def test_no_deposit_spend_limit_with_normal_registration() -> None:
    snapshot = MetricsSnapshot(
        spend=Decimal("2.50"),
        registrations=3,
        cost_per_registration=Decimal("0.80"),
        deposits=0,
    )
    reasons = evaluate_pause_reasons(snapshot, _THRESHOLDS)
    assert "Расход достиг лимита без депозитов при нормальной регистрации" in reasons


# Правило 5: не срабатывает при дорогой регистрации (уже покрывается правилом 3).
def test_no_deposit_spend_limit_skipped_when_registration_expensive() -> None:
    snapshot = MetricsSnapshot(
        spend=Decimal("2.50"),
        registrations=2,
        cost_per_registration=Decimal("1.25"),
        deposits=0,
    )
    reasons = evaluate_pause_reasons(snapshot, _THRESHOLDS)
    assert "Расход достиг лимита без депозитов при нормальной регистрации" not in reasons


# Правило 5: не срабатывает без регистраций.
def test_no_deposit_spend_limit_skipped_without_registrations() -> None:
    snapshot = MetricsSnapshot(
        spend=Decimal("2.50"),
        registrations=0,
        cost_per_registration=None,
        deposits=0,
    )
    reasons = evaluate_pause_reasons(snapshot, _THRESHOLDS)
    assert "Расход достиг лимита без депозитов при нормальной регистрации" not in reasons


# Правило 6: после первого депозита расход достиг лимита.
def test_after_deposit_spend_limit() -> None:
    snapshot = MetricsSnapshot(
        spend=Decimal("3.50"),
        registrations=5,
        cost_per_registration=Decimal("0.70"),
        deposits=1,
    )
    reasons = evaluate_pause_reasons(snapshot, _THRESHOLDS)
    assert "После первого депозита расход достиг лимита" in reasons


# Правило 6: после депозита расход ниже порога — безопасно.
def test_after_deposit_spend_below_limit_is_safe() -> None:
    snapshot = MetricsSnapshot(
        spend=Decimal("3.49"),
        registrations=5,
        cost_per_registration=Decimal("0.70"),
        deposits=1,
    )
    reasons = evaluate_pause_reasons(snapshot, _THRESHOLDS)
    assert "После первого депозита расход достиг лимита" not in reasons


# Проверяет, что одно объявление может нарушить сразу несколько правил одновременно.
def test_multiple_reasons_can_fire_at_once() -> None:
    snapshot = MetricsSnapshot(
        spend=Decimal("3.50"),
        clicks=3,
        cpc=Decimal("0.20"),
        leads=1,
        cost_per_lead=Decimal("0.60"),
        registrations=5,
        cost_per_registration=Decimal("1.10"),
        deposits=0,
    )
    reasons = evaluate_pause_reasons(snapshot, _THRESHOLDS)
    assert len(reasons) >= 3
    assert "Клик превысил допустимую долю CPA" in reasons
    assert "Лид превысил допустимую долю CPA" in reasons
    assert "Регистрация превысила допустимую долю CPA" in reasons
    assert "Набралось пять регистраций без депозитов" in reasons

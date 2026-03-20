from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.domain import DeliveryStatus
from core.rules.cpa_thresholds import ThresholdPack


@dataclass(slots=True, frozen=True)
class MetricsSnapshot:
    spend: Decimal
    clicks: int = 0
    cpc: Decimal | None = None
    leads: int = 0
    cost_per_lead: Decimal | None = None
    registrations: int = 0
    cost_per_registration: Decimal | None = None
    deposits: int = 0


@dataclass(slots=True, frozen=True)
class CleanScanState:
    streak: int = 0


@dataclass(slots=True, frozen=True)
class ResumeDecision:
    should_resume: bool
    reason: str


def evaluate_pause_reasons(snapshot: MetricsSnapshot, thresholds: ThresholdPack) -> list[str]:
    """Возвращает причины, по которым объявление нужно остановить."""

    reasons: list[str] = []

    if snapshot.cpc is not None and snapshot.cpc > thresholds.cpc_stop:
        reasons.append("Клик превысил допустимую долю CPA")
    if snapshot.cost_per_lead is not None and snapshot.cost_per_lead > thresholds.cpl_stop:
        reasons.append("Лид превысил допустимую долю CPA")
    if (
        snapshot.cost_per_registration is not None
        and snapshot.cost_per_registration > thresholds.registration_stop
    ):
        reasons.append("Регистрация превысила допустимую долю CPA")
    if snapshot.registrations >= 5 and snapshot.deposits == 0:
        reasons.append("Набралось пять регистраций без депозитов")
    if (
        snapshot.spend >= thresholds.no_deposit_spend_stop
        and snapshot.deposits == 0
        and snapshot.registrations >= 1
        and snapshot.cost_per_registration is not None
        and snapshot.cost_per_registration < thresholds.registration_stop
    ):
        reasons.append("Расход достиг лимита без депозитов при нормальной регистрации")
    if snapshot.deposits >= 1 and snapshot.spend >= thresholds.after_deposit_spend_stop:
        reasons.append("После первого депозита расход достиг лимита")

    return reasons


def evaluate_resume(
    snapshot: MetricsSnapshot,
    thresholds: ThresholdPack,
    clean_scans: CleanScanState,
    delivery_status: DeliveryStatus,
    is_blocked: bool,
) -> ResumeDecision:
    """Проверяет, можно ли вернуть объявление в ротацию."""

    if is_blocked:
        return ResumeDecision(False, "Объявление заблокировано политикой")
    if delivery_status == DeliveryStatus.NOT_DELIVERING:
        return ResumeDecision(False, "Объявление не показывается и требует ручной проверки")
    if clean_scans.streak < 2:
        return ResumeDecision(False, "Недостаточно чистых сканов подряд для безопасного включения")

    pause_reasons = evaluate_pause_reasons(snapshot, thresholds)
    if pause_reasons:
        return ResumeDecision(False, "Объявление по-прежнему нарушает стоп-метрики")

    if snapshot.leads == 0 and snapshot.spend > thresholds.cpl_stop:
        return ResumeDecision(False, "Расход уже превысил порог лида без самого лида")
    if snapshot.registrations == 0 and snapshot.spend > thresholds.registration_stop:
        return ResumeDecision(False, "Расход уже превысил порог регистрации без самой регистрации")

    return ResumeDecision(True, "Объявление снова безопасно для запуска")

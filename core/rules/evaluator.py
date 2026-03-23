from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.domain import DeliveryStatus
from core.rules.cpa_thresholds import ThresholdPack
from core.rules.types import RuleSwitches

RESUME_REASON_BLOCKED_BY_POLICY = "Объявление заблокировано политикой"
RESUME_REASON_NOT_DELIVERING = "Объявление не показывается и требует ручной проверки"
RESUME_REASON_INSUFFICIENT_CLEAN_STREAK = (
    "Недостаточно чистых сканов подряд для безопасного включения"
)
RESUME_REASON_SAFE_TO_RUN = "Объявление снова безопасно для запуска"


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


def evaluate_pause_reasons(
    snapshot: MetricsSnapshot,
    thresholds: ThresholdPack,
    switches: RuleSwitches | None = None,
) -> list[str]:
    """Возвращает причины, по которым объявление нужно остановить."""

    active_switches = switches or RuleSwitches()
    reasons: list[str] = []

    if (
        active_switches.stop_high_cpc
        and snapshot.cpc is not None
        and snapshot.cpc > thresholds.cpc_stop
    ):
        reasons.append("Клик превысил допустимую долю CPA")
    if (
        active_switches.stop_high_cpc
        and snapshot.clicks == 0
        and snapshot.spend > thresholds.cpc_stop
    ):
        reasons.append("Расход уже превысил порог клика без самого клика")
    if (
        active_switches.stop_high_cpl
        and snapshot.cost_per_lead is not None
        and snapshot.cost_per_lead > thresholds.cpl_stop
    ):
        reasons.append("Лид превысил допустимую долю CPA")
    if (
        active_switches.stop_high_cpl
        and snapshot.leads == 0
        and snapshot.spend > thresholds.cpl_stop
    ):
        reasons.append("Расход уже превысил порог лида без самого лида")
    if (
        active_switches.stop_high_cpr
        and snapshot.cost_per_registration is not None
        and snapshot.cost_per_registration > thresholds.registration_stop
    ):
        reasons.append("Регистрация превысила допустимую долю CPA")
    if (
        active_switches.stop_high_cpr
        and snapshot.registrations == 0
        and snapshot.spend > thresholds.registration_stop
    ):
        reasons.append("Расход уже превысил порог регистрации без самой регистрации")
    if (
        active_switches.stop_five_regs_without_deposit
        and snapshot.registrations >= 5
        and snapshot.deposits == 0
    ):
        reasons.append("Набралось пять регистраций без депозитов")
    if (
        active_switches.stop_spend_window_without_deposit
        and snapshot.spend >= thresholds.no_deposit_spend_stop
        and snapshot.deposits == 0
        and snapshot.registrations >= 1
        and snapshot.cost_per_registration is not None
        and snapshot.cost_per_registration < thresholds.registration_stop
    ):
        reasons.append("Расход достиг лимита без депозитов при нормальной регистрации")
    if (
        active_switches.stop_spend_after_deposit
        and snapshot.deposits >= 1
        and snapshot.spend >= thresholds.after_deposit_spend_stop
    ):
        reasons.append("После первого депозита расход достиг лимита")

    return reasons


def evaluate_resume(
    snapshot: MetricsSnapshot,
    thresholds: ThresholdPack,
    clean_scans: CleanScanState,
    delivery_status: DeliveryStatus,
    is_blocked: bool,
    switches: RuleSwitches | None = None,
) -> ResumeDecision:
    """Проверяет, можно ли вернуть объявление в ротацию."""

    if is_blocked:
        return ResumeDecision(False, RESUME_REASON_BLOCKED_BY_POLICY)
    if delivery_status == DeliveryStatus.NOT_DELIVERING:
        return ResumeDecision(False, RESUME_REASON_NOT_DELIVERING)
    if clean_scans.streak < 2:
        return ResumeDecision(False, RESUME_REASON_INSUFFICIENT_CLEAN_STREAK)

    pause_reasons = evaluate_pause_reasons(snapshot, thresholds, switches=switches)
    if pause_reasons:
        return ResumeDecision(False, pause_reasons[0])

    return ResumeDecision(True, RESUME_REASON_SAFE_TO_RUN)

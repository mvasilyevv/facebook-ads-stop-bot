from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal

from core.domain import DecisionType, DeliveryStatus, ScopePresence, TrackingMode
from core.rules import (
    CleanScanState,
    MetricsSnapshot,
    build_threshold_pack,
    evaluate_pause_reasons,
    evaluate_resume,
)
from core.rules.types import RulePercentages
from core.scanner.models import (
    ScannedAdRow,
    ScannerDecisionResult,
    ScannerPolicyFlags,
    ScanScopeSummary,
)


class ObserveScannerService:
    """Чистая логика observe-фазы без Playwright и без БД."""

    def __init__(self, percentages: RulePercentages | None = None) -> None:
        self._percentages = percentages or RulePercentages()

    def evaluate_row(
        self,
        row: ScannedAdRow,
        resolved_cpa_usd: Decimal | None,
        policy_flags: ScannerPolicyFlags | None = None,
        clean_streak: int = 0,
    ) -> ScannerDecisionResult:
        flags = policy_flags or ScannerPolicyFlags()

        if row.delivery_status == DeliveryStatus.NOT_DELIVERING:
            return ScannerDecisionResult(
                decision=DecisionType.ALERT_REJECTION,
                reason="Объявление не показывается и требует ручной проверки",
                resolved_cpa_usd=resolved_cpa_usd,
            )

        if flags.is_blocked:
            return ScannerDecisionResult(
                decision=DecisionType.SKIPPED_BY_POLICY,
                reason="Объявление заблокировано политикой",
                resolved_cpa_usd=resolved_cpa_usd,
            )

        if resolved_cpa_usd is None:
            return ScannerDecisionResult(
                decision=DecisionType.INSUFFICIENT_DATA,
                reason="Не удалось определить CPA объявления",
            )

        thresholds = build_threshold_pack(resolved_cpa_usd, self._percentages)
        snapshot = to_metrics_snapshot(row)
        pause_reasons = tuple(evaluate_pause_reasons(snapshot, thresholds))

        if row.delivery_status == DeliveryStatus.PAUSED and flags.auto_resume_enabled:
            resume_decision = evaluate_resume(
                snapshot=snapshot,
                thresholds=thresholds,
                clean_scans=CleanScanState(streak=clean_streak),
                delivery_status=row.delivery_status,
                is_blocked=flags.is_blocked,
            )
            if resume_decision.should_resume:
                return ScannerDecisionResult(
                    decision=DecisionType.WOULD_RESUME,
                    reason=resume_decision.reason,
                    resolved_cpa_usd=resolved_cpa_usd,
                    thresholds=thresholds,
                    resume_reason=resume_decision.reason,
                )

        if row.delivery_status != DeliveryStatus.PAUSED and pause_reasons:
            return ScannerDecisionResult(
                decision=DecisionType.WOULD_PAUSE,
                reason=pause_reasons[0],
                resolved_cpa_usd=resolved_cpa_usd,
                thresholds=thresholds,
                stop_reasons=pause_reasons,
            )

        if row.delivery_status == DeliveryStatus.PAUSED and pause_reasons:
            return ScannerDecisionResult(
                decision=DecisionType.KEPT_PAUSED_BY_VIABILITY,
                reason="Объявление остается на паузе — метрики всё ещё нарушают стоп-правила",
                resolved_cpa_usd=resolved_cpa_usd,
                thresholds=thresholds,
                stop_reasons=pause_reasons,
            )

        return ScannerDecisionResult(
            decision=DecisionType.NO_ACTION,
            reason="Объявление находится в допустимой зоне",
            resolved_cpa_usd=resolved_cpa_usd,
            thresholds=thresholds,
        )


def to_metrics_snapshot(row: ScannedAdRow) -> MetricsSnapshot:
    """Преобразует нормализованную строку в общий доменный снимок метрик."""

    return MetricsSnapshot(
        spend=row.spend,
        clicks=row.clicks,
        cpc=row.cpc,
        leads=row.leads,
        cost_per_lead=row.cost_per_lead,
        registrations=row.registrations,
        cost_per_registration=row.cost_per_registration,
        deposits=row.deposits,
    )


def build_scope_summary(
    rows: Iterable[ScannedAdRow],
    scanned_at: datetime | None = None,
) -> ScanScopeSummary:
    """Собирает агрегированную сводку по результатам скана."""

    collected_rows = tuple(rows)
    return ScanScopeSummary(
        rows_seen=len(collected_rows),
        rows_in_scope=sum(
            1 for row in collected_rows if row.scope_presence == ScopePresence.IN_SCOPE
        ),
        rows_not_seen_this_scan=sum(
            1 for row in collected_rows if row.scope_presence == ScopePresence.NOT_SEEN_THIS_SCAN
        ),
        rows_out_of_scope_confirmed=sum(
            1
            for row in collected_rows
            if row.scope_presence == ScopePresence.OUT_OF_SCOPE_CONFIRMED
        ),
        active_rows=sum(
            1 for row in collected_rows if row.delivery_status == DeliveryStatus.ACTIVE
        ),
        paused_rows=sum(
            1 for row in collected_rows if row.delivery_status == DeliveryStatus.PAUSED
        ),
        not_delivering_rows=sum(
            1 for row in collected_rows if row.delivery_status == DeliveryStatus.NOT_DELIVERING
        ),
        manual_blocked_rows=sum(
            1 for row in collected_rows if row.tracking_mode == TrackingMode.MANUAL_BLOCK
        ),
        read_only_rows=sum(
            1 for row in collected_rows if row.tracking_mode == TrackingMode.READ_ONLY
        ),
        unknown_rows=sum(
            1 for row in collected_rows if row.delivery_status == DeliveryStatus.UNKNOWN
        ),
        scanned_at=scanned_at,
        fb_ad_ids=tuple(row.fb_ad_id for row in collected_rows),
    )


def evaluate_scanned_row(
    row: ScannedAdRow,
    resolved_cpa_usd: Decimal | None,
    policy_flags: ScannerPolicyFlags | None = None,
    clean_streak: int = 0,
    percentages: RulePercentages | None = None,
) -> ScannerDecisionResult:
    """Удобный фасад для оценки одного объявления без создания сервиса вручную."""

    service = ObserveScannerService(percentages=percentages)
    return service.evaluate_row(
        row=row,
        resolved_cpa_usd=resolved_cpa_usd,
        policy_flags=policy_flags,
        clean_streak=clean_streak,
    )

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from core.domain import (
    ActionType,
    DecisionType,
    DeliveryStatus,
    RiskBand,
    TelegramEventType,
    TrackingMode,
)
from core.rules import MetricsSnapshot, ThresholdPack
from core.rules.types import RuleSwitches
from core.scanner import ScannedAdRow, ScannerDecisionResult, ScanScopeSummary

_WATCH_RATIO = Decimal("0.85")
_AUTO_PAUSE_ACTION_SOURCE = "автопауза"
_AUTO_RESUME_ACTION_SOURCE = "авторезюм"
_EXECUTION_STATE_NOT_REQUIRED = "NOT_REQUIRED"
_EXECUTION_STATE_SKIPPED_BY_MODE = "SKIPPED_BY_MODE"
_EXECUTION_STATE_PENDING = "PENDING"
_EXECUTION_STATE_QUEUED = "QUEUED"
_PLACEHOLDER_CAMPAIGN_NAME_PATTERN = re.compile(
    r"^(?:кампания|campaign)\s+\d{8,20}$",
    re.IGNORECASE,
)
_PLACEHOLDER_AD_NAME_PATTERN = re.compile(
    r"^(?:объявление|ad)\s+\d{8,20}$",
    re.IGNORECASE,
)


@dataclass(slots=True, frozen=True)
class EvaluatedAdRow:
    """Собранное состояние объявления после оценки и обогащения."""

    row: ScannedAdRow
    decision_result: ScannerDecisionResult
    risk_band: RiskBand
    risk_reason: str | None
    priority_score: int
    resolved_cpa_usd: Decimal | None
    offer_id: str | None
    offer_rate_version_id: str | None


def serialize_scope_summary(summary: ScanScopeSummary) -> dict[str, Any]:
    return {
        "rows_seen": summary.rows_seen,
        "rows_in_scope": summary.rows_in_scope,
        "rows_not_seen_this_scan": summary.rows_not_seen_this_scan,
        "rows_out_of_scope_confirmed": summary.rows_out_of_scope_confirmed,
        "active_rows": summary.active_rows,
        "paused_rows": summary.paused_rows,
        "not_delivering_rows": summary.not_delivering_rows,
        "manual_blocked_rows": summary.manual_blocked_rows,
        "read_only_rows": summary.read_only_rows,
        "unknown_rows": summary.unknown_rows,
        "scanned_at": summary.scanned_at.isoformat() if summary.scanned_at is not None else None,
        "fb_ad_ids": list(summary.fb_ad_ids),
    }


def build_initial_action_state(
    *,
    decision_result: ScannerDecisionResult,
    auto_pause_enabled: bool,
    auto_resume_enabled: bool,
    observe_only_enabled: bool,
) -> str:
    if decision_result.decision not in {DecisionType.WOULD_PAUSE, DecisionType.WOULD_RESUME}:
        return _EXECUTION_STATE_NOT_REQUIRED
    if observe_only_enabled:
        return _EXECUTION_STATE_SKIPPED_BY_MODE
    if decision_result.decision == DecisionType.WOULD_PAUSE:
        return _EXECUTION_STATE_QUEUED if auto_pause_enabled else _EXECUTION_STATE_SKIPPED_BY_MODE
    if decision_result.decision == DecisionType.WOULD_RESUME:
        return _EXECUTION_STATE_PENDING if auto_resume_enabled else _EXECUTION_STATE_SKIPPED_BY_MODE
    return _EXECUTION_STATE_NOT_REQUIRED


def restore_scope_from_existing_ad(
    *,
    row: ScannedAdRow,
    existing_ad: Any | None,
) -> ScannedAdRow:
    if existing_ad is None:
        return row

    campaign_name = row.campaign_name
    campaign_scope_key = row.campaign_scope_key
    adset_name = row.adset_name
    adset_scope_key = row.adset_scope_key
    ad_name = row.ad_name

    existing_campaign = getattr(existing_ad, "campaign", None)
    existing_adset = getattr(existing_ad, "adset", None)

    if (
        existing_campaign is not None
        and getattr(existing_campaign, "name", None)
        and _is_placeholder_campaign_name(row.campaign_name)
    ):
        campaign_name = existing_campaign.name
        if getattr(existing_campaign, "scope_key", None):
            campaign_scope_key = existing_campaign.scope_key

    if existing_adset is not None and getattr(existing_adset, "name", None) and not row.adset_name:
        adset_name = existing_adset.name
    if existing_adset is not None and getattr(existing_adset, "scope_key", None):
        if campaign_scope_key != row.campaign_scope_key or adset_name != row.adset_name:
            adset_scope_key = existing_adset.scope_key

    if getattr(existing_ad, "name", None) and _is_placeholder_ad_name(row.ad_name):
        ad_name = existing_ad.name

    if (
        campaign_name == row.campaign_name
        and campaign_scope_key == row.campaign_scope_key
        and adset_name == row.adset_name
        and adset_scope_key == row.adset_scope_key
        and ad_name == row.ad_name
    ):
        return row

    return replace(
        row,
        campaign_name=campaign_name,
        campaign_scope_key=campaign_scope_key,
        adset_name=adset_name,
        adset_scope_key=adset_scope_key,
        ad_name=ad_name,
    )


def restore_last_action_from_history(
    *,
    ad: Any,
    latest_successful_action: tuple[ActionType, datetime] | None,
) -> None:
    if latest_successful_action is None:
        return

    action_type, action_at = latest_successful_action
    restored_source = map_action_type_to_source(action_type)
    if restored_source is None:
        return

    last_action_at = restore_utc(getattr(ad, "last_action_at", None))
    restored_action_at = restore_utc(action_at)
    if (
        last_action_at is not None
        and restored_action_at is not None
        and last_action_at >= restored_action_at
    ):
        return

    ad.last_action_source = restored_source
    ad.last_action_at = restored_action_at


def map_action_type_to_source(action_type: ActionType) -> str | None:
    if action_type == ActionType.PAUSE:
        return _AUTO_PAUSE_ACTION_SOURCE
    if action_type == ActionType.RESUME:
        return _AUTO_RESUME_ACTION_SOURCE
    return None


def map_action_type_to_event_type(action_type: ActionType) -> TelegramEventType | None:
    if action_type == ActionType.PAUSE:
        return TelegramEventType.AD_PAUSED_BY_BOT
    if action_type == ActionType.RESUME:
        return TelegramEventType.AD_RESUMED_BY_BOT
    return None


def map_decision_to_event_type(decision: DecisionType) -> TelegramEventType | None:
    mapping = {
        DecisionType.WOULD_PAUSE: TelegramEventType.OBSERVE_WOULD_PAUSE,
        DecisionType.WOULD_RESUME: TelegramEventType.OBSERVE_WOULD_RESUME,
        DecisionType.ALERT_REJECTION: TelegramEventType.AD_REJECTED_OR_NOT_DELIVERING,
    }
    return mapping.get(decision)


def restore_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def derive_risk_band(
    *,
    row: ScannedAdRow,
    decision_result: ScannerDecisionResult,
    rule_switches: RuleSwitches,
) -> tuple[RiskBand, str | None]:
    if decision_result.decision == DecisionType.WOULD_PAUSE:
        return RiskBand.STOP, decision_result.reason
    if row.delivery_status == DeliveryStatus.PAUSED:
        return RiskBand.SAFE, None
    if row.delivery_status == DeliveryStatus.NOT_DELIVERING:
        return RiskBand.SAFE, None
    if row.tracking_mode in {TrackingMode.MANUAL_BLOCK, TrackingMode.READ_ONLY}:
        return RiskBand.SAFE, None
    thresholds = decision_result.thresholds
    if thresholds is None:
        return RiskBand.SAFE, None

    watch_reasons = evaluate_watch_reasons(
        snapshot=to_metrics_snapshot(row),
        thresholds=thresholds,
        switches=rule_switches,
    )
    if watch_reasons:
        return RiskBand.WATCH, watch_reasons[0]
    return RiskBand.SAFE, None


def compute_priority_score(
    *,
    row: ScannedAdRow,
    decision_result: ScannerDecisionResult,
    risk_band: RiskBand,
    rule_switches: RuleSwitches,
) -> int:
    if risk_band == RiskBand.SAFE:
        return 0
    thresholds = decision_result.thresholds
    if thresholds is None:
        return 100 if risk_band == RiskBand.STOP else 10

    ratios = collect_metric_ratios(
        snapshot=to_metrics_snapshot(row),
        thresholds=thresholds,
        switches=rule_switches,
    )
    max_ratio = max(ratios, default=Decimal("1"))
    base = 1000 if risk_band == RiskBand.STOP else 500
    return min(base + int(max_ratio * 100), 5000)


def evaluate_watch_reasons(
    *,
    snapshot: MetricsSnapshot,
    thresholds: ThresholdPack,
    switches: RuleSwitches,
) -> tuple[str, ...]:
    reasons: list[str] = []
    watch_cpc_stop = thresholds.cpc_stop * _WATCH_RATIO
    watch_cpl_stop = thresholds.cpl_stop * _WATCH_RATIO
    watch_cpr_stop = thresholds.registration_stop * _WATCH_RATIO
    watch_no_deposit_spend_stop = thresholds.no_deposit_spend_stop * _WATCH_RATIO
    watch_after_deposit_spend_stop = thresholds.after_deposit_spend_stop * _WATCH_RATIO
    watch_regs_without_deposit = max(int(Decimal("5") * _WATCH_RATIO), 1)

    if switches.stop_high_cpc and snapshot.cpc is not None and snapshot.cpc >= watch_cpc_stop:
        reasons.append("Клик приблизился к стоп-порогу")
    if switches.stop_high_cpc and snapshot.clicks == 0 and snapshot.spend >= watch_cpc_stop:
        reasons.append("Расход без кликов приблизился к стоп-порогу")
    if (
        switches.stop_high_cpl
        and snapshot.cost_per_lead is not None
        and snapshot.cost_per_lead >= watch_cpl_stop
    ):
        reasons.append("Лид приблизился к стоп-порогу")
    if switches.stop_high_cpl and snapshot.leads == 0 and snapshot.spend >= watch_cpl_stop:
        reasons.append("Расход без лидов приблизился к стоп-порогу")
    if (
        switches.stop_high_cpr
        and snapshot.cost_per_registration is not None
        and snapshot.cost_per_registration >= watch_cpr_stop
    ):
        reasons.append("Регистрация приблизилась к стоп-порогу")
    if switches.stop_high_cpr and snapshot.registrations == 0 and snapshot.spend >= watch_cpr_stop:
        reasons.append("Расход без регистраций приблизился к стоп-порогу")
    if (
        switches.stop_five_regs_without_deposit
        and snapshot.registrations >= watch_regs_without_deposit
        and snapshot.deposits == 0
    ):
        reasons.append("Регистрации без депозитов приблизились к стоп-порогу")
    if (
        switches.stop_spend_window_without_deposit
        and snapshot.spend >= watch_no_deposit_spend_stop
        and snapshot.deposits == 0
        and snapshot.registrations >= 1
        and snapshot.cost_per_registration is not None
        and snapshot.cost_per_registration < thresholds.registration_stop
    ):
        reasons.append("Расход без депозитов приблизился к стоп-порогу")
    if (
        switches.stop_spend_after_deposit
        and snapshot.deposits >= 1
        and snapshot.spend >= watch_after_deposit_spend_stop
    ):
        reasons.append("Расход после депозита приблизился к стоп-порогу")

    return tuple(reasons)


def collect_metric_ratios(
    *,
    snapshot: MetricsSnapshot,
    thresholds: ThresholdPack,
    switches: RuleSwitches,
) -> tuple[Decimal, ...]:
    ratios: list[Decimal] = []
    if switches.stop_high_cpc:
        if snapshot.cpc is not None and thresholds.cpc_stop > 0:
            ratios.append(snapshot.cpc / thresholds.cpc_stop)
        if snapshot.clicks == 0 and thresholds.cpc_stop > 0:
            ratios.append(snapshot.spend / thresholds.cpc_stop)
    if switches.stop_high_cpl:
        if snapshot.cost_per_lead is not None and thresholds.cpl_stop > 0:
            ratios.append(snapshot.cost_per_lead / thresholds.cpl_stop)
        if snapshot.leads == 0 and thresholds.cpl_stop > 0:
            ratios.append(snapshot.spend / thresholds.cpl_stop)
    if switches.stop_high_cpr:
        if snapshot.cost_per_registration is not None and thresholds.registration_stop > 0:
            ratios.append(snapshot.cost_per_registration / thresholds.registration_stop)
        if snapshot.registrations == 0 and thresholds.registration_stop > 0:
            ratios.append(snapshot.spend / thresholds.registration_stop)
    if switches.stop_five_regs_without_deposit:
        ratios.append(Decimal(snapshot.registrations) / Decimal("5"))
    if switches.stop_spend_window_without_deposit and thresholds.no_deposit_spend_stop > 0:
        ratios.append(snapshot.spend / thresholds.no_deposit_spend_stop)
    if switches.stop_spend_after_deposit and thresholds.after_deposit_spend_stop > 0:
        ratios.append(snapshot.spend / thresholds.after_deposit_spend_stop)
    return tuple(ratios)


def to_metrics_snapshot(row: ScannedAdRow) -> MetricsSnapshot:
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


def build_action_notification_payload(
    *,
    ad: Any,
    fb_ad_id: str,
    message: str,
) -> dict[str, Any]:
    campaign = getattr(ad, "campaign", None)
    adset = getattr(ad, "adset", None)
    return {
        "host": "worker",
        "account_name": "unknown",
        "campaign_name": getattr(campaign, "name", "") if campaign is not None else "",
        "adset_name": getattr(adset, "name", "") if adset is not None else "",
        "ad_name": getattr(ad, "name", ""),
        "fb_ad_id": fb_ad_id,
        "reason": message,
        "metrics": {},
    }


def _is_placeholder_campaign_name(value: str) -> bool:
    return bool(value and _PLACEHOLDER_CAMPAIGN_NAME_PATTERN.fullmatch(value.strip()))


def _is_placeholder_ad_name(value: str) -> bool:
    return bool(value and _PLACEHOLDER_AD_NAME_PATTERN.fullmatch(value.strip()))

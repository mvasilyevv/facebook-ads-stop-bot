from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from apps.worker.pipeline_support import (
    build_initial_action_state,
    compute_priority_score,
    derive_risk_band,
)
from core.domain import DecisionType, DeliveryStatus, RiskBand, ScopePresence, TrackingMode
from core.rules import ThresholdPack
from core.rules.types import RuleSwitches
from core.scanner import ScannedAdRow, ScannerDecisionResult


def _build_row(*, cpc: Decimal | None = None, spend: Decimal = Decimal("0.00")) -> ScannedAdRow:
    return ScannedAdRow(
        fb_ad_id="ad-1",
        campaign_scope_key="campaign:test",
        adset_scope_key="adset:test",
        campaign_name="Кампания",
        adset_name="Адсет",
        ad_name="Объявление",
        delivery_status=DeliveryStatus.ACTIVE,
        tracking_mode=TrackingMode.TRACKED,
        scope_presence=ScopePresence.IN_SCOPE,
        spend=spend,
        clicks=1 if cpc is not None else 0,
        cpc=cpc,
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
        last_seen_at=datetime(2026, 3, 23, 18, 0, tzinfo=UTC),
    )


def _build_thresholds() -> ThresholdPack:
    return ThresholdPack(
        cpc_stop=Decimal("1.00"),
        cpl_stop=Decimal("5.00"),
        registration_stop=Decimal("10.00"),
        no_deposit_spend_stop=Decimal("20.00"),
        no_deposit_spend_audit_top=Decimal("25.00"),
        after_deposit_spend_stop=Decimal("30.00"),
        after_deposit_spend_audit_top=Decimal("35.00"),
    )


# Проверяет, что hard-stop решение сразу переводит объявление в STOP band.
def test_derive_risk_band_marks_hard_stop_as_stop() -> None:
    row = _build_row(cpc=Decimal("1.20"))
    decision = ScannerDecisionResult(
        decision=DecisionType.WOULD_PAUSE,
        reason="Клик превысил допустимую долю CPA",
        resolved_cpa_usd=Decimal("50.00"),
        thresholds=_build_thresholds(),
        stop_reasons=("Клик превысил допустимую долю CPA",),
    )

    risk_band, risk_reason = derive_risk_band(
        row=row,
        decision_result=decision,
        rule_switches=RuleSwitches(),
    )

    assert risk_band == RiskBand.STOP
    assert risk_reason == "Клик превысил допустимую долю CPA"


# Проверяет, что близкие к порогу метрики попадают в WATCH band до реального стопа.
def test_derive_risk_band_marks_near_threshold_as_watch() -> None:
    row = _build_row(cpc=Decimal("0.90"))
    decision = ScannerDecisionResult(
        decision=DecisionType.NO_ACTION,
        reason="Объявление находится в допустимой зоне",
        resolved_cpa_usd=Decimal("50.00"),
        thresholds=_build_thresholds(),
    )

    risk_band, risk_reason = derive_risk_band(
        row=row,
        decision_result=decision,
        rule_switches=RuleSwitches(),
    )

    assert risk_band == RiskBand.WATCH
    assert risk_reason == "Клик приблизился к стоп-порогу"


# Проверяет, что stop-приоритет выше watch-приоритета, а queued state выставляется для паузы.
def test_compute_priority_score_orders_stop_above_watch_and_sets_queued_state() -> None:
    thresholds = _build_thresholds()
    stop_decision = ScannerDecisionResult(
        decision=DecisionType.WOULD_PAUSE,
        reason="Стоп",
        resolved_cpa_usd=Decimal("50.00"),
        thresholds=thresholds,
        stop_reasons=("Стоп",),
    )
    watch_decision = ScannerDecisionResult(
        decision=DecisionType.NO_ACTION,
        reason="Норма",
        resolved_cpa_usd=Decimal("50.00"),
        thresholds=thresholds,
    )

    stop_score = compute_priority_score(
        row=_build_row(cpc=Decimal("1.10")),
        decision_result=stop_decision,
        risk_band=RiskBand.STOP,
        rule_switches=RuleSwitches(),
    )
    watch_score = compute_priority_score(
        row=_build_row(cpc=Decimal("0.90")),
        decision_result=watch_decision,
        risk_band=RiskBand.WATCH,
        rule_switches=RuleSwitches(),
    )
    action_state = build_initial_action_state(
        decision_result=stop_decision,
        auto_pause_enabled=True,
        auto_resume_enabled=False,
        observe_only_enabled=False,
    )

    assert stop_score > watch_score > 0
    assert action_state == "QUEUED"

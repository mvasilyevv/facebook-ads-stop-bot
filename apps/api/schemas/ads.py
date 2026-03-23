from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from apps.api.schemas.common import (
    ActionJobStatus,
    DecisionKind,
    DeliveryStatus,
    ExecutionState,
    FastStopState,
    RiskBand,
    ScopePresence,
    TrackingMode,
)


class AdSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    fb_ad_id: str
    campaign_name: str
    adset_name: str
    ad_name: str
    delivery_status: DeliveryStatus = DeliveryStatus.UNKNOWN
    tracking_mode: TrackingMode = TrackingMode.TRACKED
    scope_presence: ScopePresence = ScopePresence.NOT_SEEN_THIS_SCAN
    last_seen_at: datetime | None = None
    last_decision: DecisionKind = DecisionKind.NO_ACTION
    last_decision_reason: str | None = None
    last_decision_at: datetime | None = None
    last_execution_state: ExecutionState | None = None
    last_action_source: str | None = None
    last_action_at: datetime | None = None
    last_action_message: str | None = None
    risk_band: RiskBand = RiskBand.SAFE
    fast_stop_state: FastStopState = FastStopState.IDLE
    watch_reason: str | None = None
    queued_action_status: ActionJobStatus | None = None
    priority_score: int = 0
    resolved_cpa_usd: Decimal | None = None
    spend: Decimal | None = None
    clicks: int | None = None
    cpc: Decimal | None = None
    leads: int | None = None
    cost_per_lead: Decimal | None = None
    registrations: int | None = None
    cost_per_registration: Decimal | None = None
    deposits: int | None = None


class AdDetail(AdSummary):
    campaign_scope_key: str
    adset_scope_key: str
    last_scan_run_id: str | None = None
    created_at: datetime
    updated_at: datetime


class AdBlockRequest(BaseModel):
    reason: str = Field(min_length=1, default="Заблокировано оператором")
    created_by: str = Field(min_length=1, default="operator")


class AdActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    message: str
    ad: AdDetail

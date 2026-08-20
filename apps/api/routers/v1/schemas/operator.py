"""Versioned contracts for the operator control room.

The server owns freshness, timezone boundaries and state semantics.  ``None``
is unknown; a numeric zero is returned only when the source confirmed it.
Money and precise ratios are serialized as decimal strings.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field

from apps.api.schemas.problem import ApiProblem as ApiProblem


class DataState(StrEnum):
    READY = "ready"
    EMPTY = "empty"
    PARTIAL = "partial"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class OperatorSeverity(StrEnum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class OperatorActionState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class OperatorIssue(BaseModel):
    code: str
    title: str
    detail: str | None
    severity: OperatorSeverity
    correlation_id: str | None


T = TypeVar("T")


class OperatorSection(BaseModel, Generic[T]):
    state: DataState
    as_of: datetime | None
    freshness_seconds: int | None = Field(..., ge=0)
    sources: list[str]
    issues: list[OperatorIssue]
    data: T | None


class OperatorCabinetDay(BaseModel):
    starts_at: datetime
    ends_at: datetime


class OperatorScopeEvidence(BaseModel):
    """Validated account context shared by operator and analytics responses."""

    account_ids: list[str]
    display_timezone: str
    cabinet_timezone: str | None
    cabinet_timezone_state: Literal["single", "mixed", "unknown"]
    missing_timezone_account_ids: list[str]
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    currency_state: Literal["single", "mixed", "unknown"]
    missing_currency_account_ids: list[str]
    currency_observed_at: datetime | None


class OperatorAccount(BaseModel):
    id: str | None
    name: str | None


class OperatorSnapshotMeta(BaseModel):
    revision: str
    sequence: int = Field(ge=0)
    generated_at: datetime
    timezone: str
    cabinet_timezone: str | None
    cabinet_timezone_known: bool
    cabinet_timezone_state: Literal["single", "mixed", "unknown"]
    missing_timezone_account_ids: list[str]
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    currency_state: Literal["single", "mixed", "unknown"]
    missing_currency_account_ids: list[str]
    currency_observed_at: datetime | None
    window: Literal["today", "24h", "7d", "30d"]
    account: OperatorAccount
    cabinet_day: OperatorCabinetDay


class OperatorAttentionTarget(BaseModel):
    kind: Literal["ad", "campaign", "account", "system"]
    id: str | None
    label: str | None


class OperatorAttentionAction(BaseModel):
    label: str
    href: str


class OperatorAttentionItem(BaseModel):
    id: str
    kind: Literal["incident", "action", "source", "recommendation"]
    severity: OperatorSeverity
    title: str
    summary: str
    reason: str | None
    occurred_at: datetime
    target: OperatorAttentionTarget
    action: OperatorAttentionAction | None
    recovery_action: Literal["retry_scan"] | None


class OperatorAttentionData(BaseModel):
    items: list[OperatorAttentionItem]


class OperatorIncidentItem(BaseModel):
    """Safe incident projection for list and detail operator surfaces."""

    id: str
    severity: OperatorSeverity
    status: Literal["open", "acknowledged", "executing", "resolved", "failed"]
    title: str
    summary: str | None
    reason: str | None
    occurred_at: datetime
    account_id: str | None
    target: OperatorAttentionTarget
    action: OperatorAttentionAction
    requires_usd_evidence: bool


class OperatorIncidentsResponse(BaseModel):
    state: DataState
    as_of: datetime
    freshness_seconds: int = Field(ge=0)
    sources: list[str]
    issues: list[OperatorIssue]
    scope: OperatorScopeEvidence
    items: list[OperatorIncidentItem]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    pages: int = Field(ge=0)


class OperatorIncidentDetailResponse(BaseModel):
    state: DataState
    as_of: datetime
    freshness_seconds: int = Field(ge=0)
    sources: list[str]
    issues: list[OperatorIssue]
    timezone: str
    timezone_known: bool
    scope: OperatorScopeEvidence
    incident: OperatorIncidentItem


class OperatorEconomyTotals(BaseModel):
    spend: str | None
    base: str | None
    stop: str | None
    base_delta: str | None


class OperatorSpendPoint(BaseModel):
    at: datetime
    actual: str | None
    base: str | None
    stop: str | None


class OperatorEconomyData(BaseModel):
    totals: OperatorEconomyTotals
    series: list[OperatorSpendPoint]


class OperatorCabinetLedgerRow(BaseModel):
    id: str
    name: str
    timezone: str | None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    state: DataState
    severity: OperatorSeverity
    as_of: datetime | None
    freshness_seconds: int | None = Field(..., ge=0)
    cabinet_day: OperatorCabinetDay | None
    totals: OperatorEconomyTotals
    risk_label: str
    risk_reason: str | None
    issues: list[OperatorIssue]
    action: OperatorAttentionAction


class OperatorCurrencyGroup(BaseModel):
    id: str
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    state: DataState
    severity: OperatorSeverity
    as_of: datetime | None
    freshness_seconds: int | None = Field(..., ge=0)
    totals: OperatorEconomyTotals
    cabinets: list[OperatorCabinetLedgerRow]


class OperatorPortfolioData(BaseModel):
    currency_groups: list[OperatorCurrencyGroup]


class OperatorFunnelStage(BaseModel):
    key: Literal["clicks", "registrations", "ftd", "confirmed_deposits"]
    label: str
    count: int | None
    conversion: str | None
    cost: str | None


class OperatorFunnelData(BaseModel):
    stages: list[OperatorFunnelStage]


class OperatorActionItem(BaseModel):
    id: str
    public_id: str
    kind: Literal["pause", "activate", "scan", "create", "duplicate", "other"]
    state: OperatorActionState
    title: str
    # Запуск залива, которому принадлежит действие. Без него экран действия
    # не может показать сам залив — только собственный конвейер обработки.
    run_id: str | None = None
    target_id: str | None = None
    target_label: str | None
    requested_at: datetime
    updated_at: datetime
    requested_by: str | None
    reason: str | None
    correlation_id: str
    account_id: str | None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    cabinet_timezone: str | None
    account_context_observed_at: datetime | None
    account_context_issues: list[str]


class OperatorActionsData(BaseModel):
    items: list[OperatorActionItem]


class OperatorWorkerState(BaseModel):
    id: str
    label: str
    severity: OperatorSeverity
    status: str
    last_activity_at: datetime | None


class OperatorSystemData(BaseModel):
    severity: OperatorSeverity
    monitoring_enabled: bool | None
    last_scan_at: datetime | None
    next_scan_at: datetime | None
    workers: list[OperatorWorkerState]


class OperatorActionsResponse(BaseModel):
    state: DataState
    as_of: datetime | None
    freshness_seconds: int | None = Field(..., ge=0)
    sources: list[str]
    issues: list[OperatorIssue]
    scope: OperatorScopeEvidence
    items: list[OperatorActionItem]
    next_cursor: int | None


class OperatorEventItem(BaseModel):
    """One immutable alert or terminal command event in the operator feed."""

    event_type: Literal["alert", "task"]
    ts: datetime
    fb_ad_id: str | None = None
    ad_name: str | None = None
    campaign_id: str | None = None
    campaign_name: str | None = None
    stage: str | None = None
    rule_codes: list[str] | None = None
    task_type: str | None = None
    task_status: str | None = None


class OperatorAdMetrics(BaseModel):
    spend: str | None
    impressions: int | None
    clicks: int | None
    registrations: int | None
    ftd: int | None
    confirmed_deposits: int | None
    cpc: str | None
    cost_per_registration: str | None
    frequency: str | None
    cost_per_ftd: str | None


class OperatorRuleContext(BaseModel):
    offer_code: str | None
    rule_code: str | None
    rule_title: str | None
    value: str | None
    threshold: str | None
    percent_to_stop: str | None
    # None означает «неизвестно»: у строки нет подтверждённой оценки правил.
    # Отличается от "none" — «правила проверены, ничего не сработало». Смешивать
    # их нельзя: это money-состояние, и unknown не должен выглядеть спокойным.
    stage: Literal["none", "warning", "stop"] | None


class OperatorAdRow(BaseModel):
    id: str
    fb_ad_id: str
    name: str
    campaign_id: str
    campaign_name: str
    adset_id: str
    adset_name: str
    account_id: str | None
    delivery_status: str | None = Field(
        description=(
            "Точный нормализованный Meta effective_status (например ACTIVE, "
            "DISAPPROVED, WITH_ISSUES, PENDING_REVIEW, ADSET_PAUSED или "
            "CAMPAIGN_PAUSED); null означает, что статус не подтверждён."
        )
    )
    data_state: DataState
    severity: OperatorSeverity
    as_of: datetime | None
    metrics: OperatorAdMetrics
    rule_context: OperatorRuleContext
    active_action: OperatorActionItem | None


class OperatorApproachingStopData(BaseModel):
    items: list[OperatorAdRow]


class OperatorAdsResponse(BaseModel):
    state: DataState
    as_of: datetime | None
    freshness_seconds: int | None = Field(..., ge=0)
    sources: list[str]
    issues: list[OperatorIssue]
    scope: OperatorScopeEvidence
    rows: list[OperatorAdRow]
    page: int
    page_size: int
    total: int
    pages: int


class OperatorSnapshot(BaseModel):
    meta: OperatorSnapshotMeta
    attention: OperatorSection[OperatorAttentionData]
    approaching_stop: OperatorSection[OperatorApproachingStopData]
    portfolio: OperatorSection[OperatorPortfolioData]
    economy: OperatorSection[OperatorEconomyData]
    funnel: OperatorSection[OperatorFunnelData]
    actions: OperatorSection[OperatorActionsData]
    system: OperatorSection[OperatorSystemData]


class OperatorCommandResponse(BaseModel):
    task_id: int
    public_id: str
    state: OperatorActionState
    created: bool
    correlation_id: str


class OperatorAdCommandRequest(BaseModel):
    """Optimistic precondition captured from the confirmed ad row."""

    expected_delivery_status: str = Field(min_length=1, max_length=64)
    expected_as_of: datetime


class OperatorIncidentAckResponse(BaseModel):
    incident_id: str
    status: Literal["acknowledged"]
    acknowledged_at: datetime
    correlation_id: str


__all__ = [name for name in globals() if name.startswith(("ApiProblem", "DataState", "Operator"))]

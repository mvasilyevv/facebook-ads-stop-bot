"""Schemas for the unified operator analytics endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from apps.api.routers.v1.schemas.operator import DataState, OperatorScopeEvidence

AnalyticsLevel = Literal["campaign", "adset", "ad"]


class AnalyticsWindowOut(BaseModel):
    from_iso: datetime
    to_iso: datetime
    is_live: bool
    timezone: str | None
    timezone_known: bool
    timezone_state: Literal["single", "mixed", "unknown"]
    missing_timezone_account_ids: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    cabinet_day_note: str | None = None


class AnalyticsSourceOut(BaseModel):
    source: Literal["meta", "tracker"]
    status: Literal["good", "degraded", "missing", "unknown"]
    last_event_at: datetime | None = None
    lag_seconds: int | None = None
    unmatched_events: int = 0
    timezone_known: bool | None = None
    missing_timezone_account_ids: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    note: str | None = None


class AnalyticsSourcesOut(BaseModel):
    meta: AnalyticsSourceOut
    tracker: AnalyticsSourceOut


class AnalyticsLiveBudgetOut(BaseModel):
    stage: Literal["click", "lead", "registration", "deposit", "mixed"]
    base_unit: str | None = None
    stop_unit: str | None = None
    quantity: int | None = None
    base_budget: str
    stop_budget: str
    base_delta: str
    stop_delta: str


class AnalyticsMetricsOut(BaseModel):
    spend: str | None
    impressions: int | None
    clicks: int | None
    leads: int | None
    registrations: int | None
    ftds: int | None
    confirmed_deposits: int | None
    redeposits: int | None
    revenue: str | None
    cpc: str | None = None
    ctr_pct: str | None = None
    click_registration_cr_pct: str | None = None
    registration_ftd_cr_pct: str | None = None
    cost_per_registration: str | None = None
    cost_per_ftd: str | None = None
    roi_pct: str | None = None
    roas: str | None = None


class AnalyticsPerformanceRowOut(AnalyticsMetricsOut):
    id: str
    fb_id: str | None = None
    name: str
    level: AnalyticsLevel
    parent_id: str | None = None
    parent_name: str | None = None
    has_children: bool = False
    ad_account_id: str | None = None
    cabinet_timezone: str | None
    timezone_known: bool
    timezone_state: Literal["single", "mixed", "unknown"]
    offer_id: str | None = None
    offer_code: str | None = None
    state: DataState
    issues: list[str] = Field(default_factory=list)
    live_budget: AnalyticsLiveBudgetOut | None = None
    budget_unavailable_reason: str | None = None


class AnalyticsPaginationOut(BaseModel):
    page: int
    page_size: int
    total: int
    pages: int


class AnalyticsFilterOptionOut(BaseModel):
    value: str
    label: str


class AnalyticsFilterOptionsOut(BaseModel):
    accounts: list[AnalyticsFilterOptionOut] = Field(default_factory=list)
    offers: list[AnalyticsFilterOptionOut] = Field(default_factory=list)
    campaigns: list[AnalyticsFilterOptionOut] = Field(default_factory=list)


class AnalyticsPerformanceOut(BaseModel):
    state: DataState
    as_of: datetime | None
    freshness_seconds: int | None = Field(ge=0)
    issues: list[str] = Field(default_factory=list)
    scope: OperatorScopeEvidence
    window: AnalyticsWindowOut
    sources: AnalyticsSourcesOut
    totals: AnalyticsMetricsOut
    total_live_budget: AnalyticsLiveBudgetOut | None = None
    total_budget_unavailable_reason: str | None = None
    pagination: AnalyticsPaginationOut
    filter_options: AnalyticsFilterOptionsOut
    rows: list[AnalyticsPerformanceRowOut]


class AnalyticsBudgetPointOut(BaseModel):
    ts: datetime
    actual: str | None
    base: str | None
    stop: str | None
    available_ads: int = 0
    unavailable_ads: int = 0


class AnalyticsLiveBudgetSeriesOut(BaseModel):
    state: DataState
    as_of: datetime | None
    freshness_seconds: int | None = Field(ge=0)
    sources: AnalyticsSourcesOut
    issues: list[str] = Field(default_factory=list)
    scope: OperatorScopeEvidence
    window: AnalyticsWindowOut
    points: list[AnalyticsBudgetPointOut]


class AnalyticsDaypartCellOut(BaseModel):
    weekday: int = Field(ge=1, le=7)
    hour: int = Field(ge=0, le=23)
    clicks: int | None
    registrations: int | None
    ftds: int | None


class AnalyticsDaypartOut(BaseModel):
    state: DataState
    as_of: datetime | None
    freshness_seconds: int | None = Field(ge=0)
    sources: AnalyticsSourcesOut
    issues: list[str] = Field(default_factory=list)
    scope: OperatorScopeEvidence
    timezone: str
    from_iso: datetime
    to_iso: datetime
    cells: list[AnalyticsDaypartCellOut]


__all__ = [name for name in globals() if name.startswith("Analytics")]

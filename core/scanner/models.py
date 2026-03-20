from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from core.domain import DecisionType, DeliveryStatus, ScopePresence, TrackingMode
from core.rules import ThresholdPack


@dataclass(slots=True, frozen=True)
class ScannedAdRow:
    """Нормализованная строка объявления, полученная после сканирования scope."""

    fb_ad_id: str
    campaign_id: str
    adset_id: str
    campaign_name: str
    adset_name: str
    ad_name: str
    delivery_status: DeliveryStatus
    tracking_mode: TrackingMode
    scope_presence: ScopePresence
    spend: Decimal
    clicks: int = 0
    cpc: Decimal | None = None
    leads: int = 0
    cost_per_lead: Decimal | None = None
    registrations: int = 0
    cost_per_registration: Decimal | None = None
    deposits: int = 0
    last_seen_at: datetime | None = None
    account_name: str | None = None
    resolved_offer_id: str | None = None
    resolved_offer_code: str | None = None


@dataclass(slots=True, frozen=True)
class ScanScopeSummary:
    """Сводка по итогам одного прохода по Ads scope."""

    rows_seen: int
    rows_in_scope: int
    rows_not_seen_this_scan: int
    rows_out_of_scope_confirmed: int
    active_rows: int
    paused_rows: int
    not_delivering_rows: int
    manual_blocked_rows: int
    read_only_rows: int
    unknown_rows: int
    scanned_at: datetime | None = None
    fb_ad_ids: tuple[str, ...] = field(default_factory=tuple)


@dataclass(slots=True, frozen=True)
class ScannerPolicyFlags:
    """Политические флаги observe-фазы."""

    is_blocked: bool = False
    auto_resume_enabled: bool = False


@dataclass(slots=True, frozen=True)
class ScannerDecisionResult:
    """Итог оценки одного объявления в observe-фазе."""

    decision: DecisionType
    reason: str
    resolved_cpa_usd: Decimal | None = None
    thresholds: ThresholdPack | None = None
    stop_reasons: tuple[str, ...] = field(default_factory=tuple)
    resume_reason: str | None = None

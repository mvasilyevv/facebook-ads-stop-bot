from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from core.domain import DecisionType, DeliveryStatus, ScopePresence, TrackingMode
from core.rules import ThresholdPack

_NON_KEY_CHARS = re.compile(r"[^\w]+", re.UNICODE)
_DASHES = re.compile(r"-+")


def normalize_scope_fragment(value: str) -> str:
    """Приводит произвольную строку к безопасному фрагменту внутреннего scope key."""

    normalized = value.casefold().strip()
    normalized = _NON_KEY_CHARS.sub("-", normalized).replace("_", "-")
    normalized = _DASHES.sub("-", normalized).strip("-")
    return normalized or "unknown"


def build_campaign_scope_key(campaign_name: str, account_name: str | None = None) -> str:
    """Строит внутренний ключ кампании из имени и, при наличии, имени аккаунта."""

    parts = ["campaign"]
    if account_name is not None and account_name.strip():
        parts.append(normalize_scope_fragment(account_name))
    parts.append(normalize_scope_fragment(campaign_name))
    return ":".join(parts)


def build_adset_scope_key(adset_name: str, campaign_scope_key: str | None = None) -> str:
    """Строит внутренний ключ адсета из собственного имени и ключа кампании."""

    parts = ["adset"]
    if campaign_scope_key is not None and campaign_scope_key.strip():
        parts.append(campaign_scope_key.casefold().strip())
    parts.append(normalize_scope_fragment(adset_name))
    return ":".join(parts)


@dataclass(slots=True, frozen=True)
class ScannedAdRow:
    """Нормализованная строка объявления, полученная после сканирования scope."""

    fb_ad_id: str
    campaign_scope_key: str
    adset_scope_key: str
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

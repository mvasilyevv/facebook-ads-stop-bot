from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class TelegramEventType(StrEnum):
    AD_PAUSED_BY_BOT = "AD_PAUSED_BY_BOT"
    AD_RESUMED_BY_BOT = "AD_RESUMED_BY_BOT"
    AD_REJECTED_OR_NOT_DELIVERING = "AD_REJECTED_OR_NOT_DELIVERING"
    OBSERVE_WOULD_PAUSE = "OBSERVE_WOULD_PAUSE"
    OBSERVE_WOULD_RESUME = "OBSERVE_WOULD_RESUME"
    WORKER_ERROR = "WORKER_ERROR"
    SCOPE_INVALID = "SCOPE_INVALID"


@dataclass(slots=True, frozen=True)
class TelegramEventPayload:
    host: str
    account_name: str
    campaign_name: str
    adset_name: str
    ad_name: str
    fb_ad_id: str
    reason: str
    metrics: dict[str, str | int]
    delivery_before: str | None = None
    delivery_after: str | None = None
    rule_id: str | None = None
    decided_at: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class TelegramEvent:
    event_type: TelegramEventType
    payload: TelegramEventPayload
    dedupe_key: str
    created_at: datetime

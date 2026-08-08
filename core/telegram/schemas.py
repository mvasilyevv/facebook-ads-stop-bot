# -*- coding: utf-8 -*-
"""Typed contracts for durable Telegram notifications and webhook updates."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

NotificationSeverity = Literal["ok", "warning", "critical", "unknown"]
RecipientRole = Literal["owner", "recipient"]


class NotificationActionSpec(BaseModel):
    """Action capability minted for one recipient at delivery time."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_.-]*$")
    label: str = Field(min_length=1, max_length=32)
    kind: Literal["pause_ad", "activate_ad", "ack_incident"]
    target_type: Literal["fb_ad", "incident"]
    target_id: str = Field(min_length=1, max_length=160)
    target_payload: dict[str, Any] = Field(default_factory=dict)
    required_role: RecipientRole = "owner"
    expires_in_seconds: int = Field(default=3600, ge=60, le=7 * 24 * 3600)


class NotificationNavigationTarget(BaseModel):
    """Internal destination that becomes a recipient-bound opaque capability."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["ad", "action", "incident"]
    target_id: str = Field(min_length=1, max_length=160)


class NotificationCardFacts(BaseModel):
    """Renderer input deliberately constrained to a short operator card."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=280)
    lines: list[str] = Field(default_factory=list, max_length=5)
    risk: str | None = Field(default=None, max_length=180)
    status: str | None = Field(default=None, max_length=80)
    open_target: NotificationNavigationTarget | None = None
    # Reissued incident snapshots carry their compare-and-swap identity inside
    # the persisted event so a stale delayed delivery can be rejected at claim.
    incident_generation: int | None = Field(default=None, ge=1)
    incident_status: Literal["open", "acknowledged", "executing", "resolved", "failed"] | None = (
        None
    )


class NotificationEventSpec(BaseModel):
    """Business-side event accepted by the transactional outbox service."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_type: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_.-]*$")
    severity: NotificationSeverity
    audience: Literal["owners", "all", "explicit"] = "owners"
    template_version: int = Field(default=1, ge=1)
    facts: NotificationCardFacts
    actions: list[NotificationActionSpec] = Field(default_factory=list, max_length=1)
    dedupe_key: str = Field(min_length=1, max_length=200)
    incident_id: uuid.UUID | None = None
    correlation_id: uuid.UUID | None = None
    scheduled_at: datetime | None = None
    explicit_recipient_ids: list[uuid.UUID] = Field(default_factory=list, max_length=1000)

    @field_validator("explicit_recipient_ids")
    @classmethod
    def unique_recipients(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        return list(dict.fromkeys(value))

    @field_validator("scheduled_at")
    @classmethod
    def scheduled_at_must_be_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("scheduled_at must be timezone-aware")
        return value


class TelegramWebhookUpdate(BaseModel):
    """Minimum Bot API update envelope; unknown fields remain durable verbatim."""

    model_config = ConfigDict(extra="allow")

    update_id: int = Field(ge=0)


__all__ = [
    "NotificationActionSpec",
    "NotificationCardFacts",
    "NotificationEventSpec",
    "NotificationNavigationTarget",
    "NotificationSeverity",
    "RecipientRole",
    "TelegramWebhookUpdate",
]

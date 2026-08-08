# -*- coding: utf-8 -*-
"""Pydantic-схемы для роутера settings_telegram (схема БД)."""

from __future__ import annotations

from datetime import datetime, time
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TelegramSettingsResponse(BaseModel):
    """Ответ на GET /settings/telegram — публичные поля без bot_token_encrypted."""

    model_config = ConfigDict(from_attributes=True)

    is_authorized: bool = False
    bot_username: str | None = None
    auth_deep_link: str | None = None
    activation_command: str | None = None
    auth_invite_expires_at: datetime | None = None
    # Web App URL (Telegram Mini App). Runtime source: system_config only.
    web_app_url: str | None = None
    menu_sync_state: Literal["synced", "incomplete"] | None = None


class TelegramTokenRequest(BaseModel):
    """Тело PUT /settings/telegram/token — новый токен бота в открытом виде."""

    bot_token: str = Field(..., min_length=1, description="Telegram Bot API токен")


class TelegramWebAppUrlRequest(BaseModel):
    """Тело PUT /settings/telegram/web-app-url — URL Mini App (пусто = очистить)."""

    web_app_url: str | None = Field(
        default=None, description="HTTPS-URL Mini App; пусто/None — использовать .env"
    )


class TelegramRecipientResponse(BaseModel):
    """Одна строка telegram_recipients для GET /settings/telegram/recipients."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    chat_id: int
    username: str | None = None
    role: str
    created_at: datetime


class TelegramRecipientsListResponse(BaseModel):
    """Список получателей для GET /settings/telegram/recipients."""

    recipients: list[TelegramRecipientResponse]
    total: int


class TelegramInviteResponse(BaseModel):
    """Готовый invite-код, команда и опциональная Telegram deep-link."""

    code: str
    expires_at: datetime
    role: str = "recipient"
    auth_deep_link: str | None = None
    activation_command: str


PreferenceThreshold = Literal["off", "inherit", "ok", "warning", "critical", "unknown"]


class TelegramRecipientPreferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = Field(default="Europe/Kaliningrad", min_length=1, max_length=64)
    min_severity: Literal["ok", "warning", "critical", "unknown"] = "warning"
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    digest_local_time: time | None = None
    categories: dict[str, PreferenceThreshold] = Field(default_factory=dict)
    is_enabled: bool = True

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown IANA timezone") from exc
        return value

    @field_validator("categories")
    @classmethod
    def validate_categories(
        cls, value: dict[str, PreferenceThreshold]
    ) -> dict[str, PreferenceThreshold]:
        if len(value) > 32:
            raise ValueError("at most 32 category overrides are allowed")
        for key in value:
            if not key or len(key) > 64:
                raise ValueError("category keys must contain 1..64 characters")
        return value

    @model_validator(mode="after")
    def validate_quiet_hours_pair(self):
        if (self.quiet_hours_start is None) != (self.quiet_hours_end is None):
            raise ValueError("quiet_hours_start and quiet_hours_end must be set together")
        return self


class TelegramRecipientPreferenceResponse(TelegramRecipientPreferenceRequest):
    recipient_id: str
    updated_at: datetime | None = None


class TelegramDeliveryErrorSummary(BaseModel):
    delivery_id: int
    state: str
    error_code: str
    updated_at: datetime
    correlation_id: str


class TelegramNotificationDiagnosticsResponse(BaseModel):
    as_of: datetime
    webhook_state: Literal[
        "unconfigured",
        "pending",
        "applying",
        "retry",
        "configured",
        "failed",
    ]
    webhook_generation: int
    webhook_applied_generation: int | None
    webhook_desired_url: str | None
    webhook_remote_url: str | None
    webhook_remote_url_matches: bool
    webhook_secret_digest_present: bool
    webhook_remote_pending_update_count: int | None
    webhook_remote_last_error_at: datetime | None
    webhook_remote_last_error_message: str | None
    webhook_checked_at: datetime | None
    webhook_configured_at: datetime | None
    webhook_last_error_code: str | None
    webhook_last_error_detail: str | None
    gateway_state: Literal["configured", "auth_error", "unconfigured"]
    outbox_state: Literal["idle", "active", "degraded"]
    last_webhook_update_at: datetime | None
    inbox_counts: dict[str, int]
    delivery_counts: dict[str, int]
    command_reply_counts: dict[str, int]
    oldest_pending_at: datetime | None
    active_recipients: int
    enabled_recipients: int
    auth_incident_active: bool
    recent_errors: list[TelegramDeliveryErrorSummary]

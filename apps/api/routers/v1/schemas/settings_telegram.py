# -*- coding: utf-8 -*-
"""Pydantic-схемы для роутера settings_telegram (схема БД)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TelegramSettingsResponse(BaseModel):
    """Ответ на GET /settings/telegram — публичные поля без bot_token_encrypted."""

    model_config = ConfigDict(from_attributes=True)

    is_authorized: bool = False
    poller_status: str = "OFFLINE"
    bot_username: str | None = None
    auth_deep_link: str | None = None
    activation_command: str = "/start auth"
    chat_id: str | None = None


class TelegramTokenRequest(BaseModel):
    """Тело PUT /settings/telegram/token — новый токен бота в открытом виде."""

    bot_token: str = Field(..., min_length=1, description="Telegram Bot API токен")


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
    """Ответ на POST /settings/telegram/recipients/invite."""

    code: str
    expires_at: datetime

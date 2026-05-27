# -*- coding: utf-8 -*-
"""Pydantic-схемы для роутера settings_vision (v2-схема БД)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class VisionSettingsResponse(BaseModel):
    """Ответ на GET /settings/vision — публичные поля VisionConfig + runtime из Redis."""

    model_config = ConfigDict(from_attributes=True)

    has_token: bool = False
    profile_id: str | None = None
    # TODO: когда добавим колонку auto_restart_on_missing_cdp в vision_config — читать из БД.
    auto_restart_on_missing_cdp: bool = True
    runtime_status: str | None = None
    runtime_status_message: str | None = None
    cdp_ready: bool = False
    cdp_port: int | None = None


class VisionSettingsUpdateRequest(BaseModel):
    """Тело PUT /settings/vision — обновить x_token и/или profile_id."""

    x_token: str | None = None
    profile_id: str | None = None


class VisionReconnectResponse(BaseModel):
    """Ответ на POST /vision/reconnect."""

    status: str = "reconnected"


class VisionProfileItem(BaseModel):
    """Один профиль Vision из списка."""

    id: str
    name: str
    is_active: bool = False


class VisionProfilesResponse(BaseModel):
    """Ответ на GET /vision/profiles."""

    profiles: list[VisionProfileItem]
    total: int

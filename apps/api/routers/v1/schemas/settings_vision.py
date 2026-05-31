# -*- coding: utf-8 -*-
"""Pydantic-схемы для роутера settings_vision (схема БД)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class VisionSettingsResponse(BaseModel):
    """Ответ на GET /settings/vision — публичные поля VisionConfig + runtime из Redis."""

    model_config = ConfigDict(from_attributes=True)

    has_token: bool = False
    profile_id: str | None = None
    auto_restart_on_missing_cdp: bool = True
    runtime_status: str | None = None
    runtime_status_message: str | None = None
    cdp_ready: bool = False
    cdp_port: int | None = None


class VisionSettingsUpdateRequest(BaseModel):
    """Тело PUT /settings/vision — обновить x_token / profile_id / флаг self-heal."""

    x_token: str | None = None
    profile_id: str | None = None
    # None = не трогать; bool = выставить флаг self-heal Vision-сессии.
    auto_restart_on_missing_cdp: bool | None = None


class VisionReconnectResponse(BaseModel):
    """Ответ на POST /vision/reconnect."""

    status: str = "reconnected"

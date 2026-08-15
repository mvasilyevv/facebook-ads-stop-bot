# -*- coding: utf-8 -*-
"""Pydantic-схемы для роутера settings_vision (схема БД)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, SecretStr


class VisionSettingsResponse(BaseModel):
    """Public Vision configuration plus a direct browser-agent channel probe."""

    model_config = ConfigDict(from_attributes=True)

    has_token: bool = False
    has_cloud_credentials: bool = False
    has_cloud_username: bool = False
    has_cloud_password: bool = False
    has_team_id: bool = False
    has_folder_id: bool = False
    profile_id: str | None = None
    configuration_revision: str | None = None
    channel_status: Literal["READY", "DEGRADED", "UNAVAILABLE", "UNKNOWN"] = "UNKNOWN"
    channel_reason: Literal[
        "TOKEN_MISSING",
        "TOKEN_REJECTED",
        "CLOUD_CREDENTIALS_MISSING",
        "PROFILE_NOT_CONFIGURED",
        "PROFILE_NOT_FOUND",
        "CLOUD_UNAVAILABLE",
        "BROWSER_UNAVAILABLE",
        "READY",
        "UNKNOWN",
    ] = "UNKNOWN"
    channel_message: str | None = None
    channel_next_step: str | None = None
    required_browser_contract_version: int
    browser_contract_version: int | None = None
    browser_contract_compatible: bool = False
    browser_session_id: str | None = None
    live_profile_id: str | None = None
    graph_probe_performed: bool = False
    graph_probe_ok: bool = False


class VisionSettingsUpdateRequest(BaseModel):
    """Тело PUT /settings/vision — обновить токен, профиль и cloud-креды."""

    model_config = ConfigDict(extra="forbid")

    x_token: str | None = None
    profile_id: str | None = None
    username: SecretStr | None = None
    password: SecretStr | None = None
    team_id: SecretStr | None = None
    folder_id: SecretStr | None = None


class VisionReconnectResponse(BaseModel):
    """Ответ на POST /vision/reconnect."""

    status: str = "reconnected"


class VisionEnsureCdpResponse(BaseModel):
    """Ответ на POST /vision/ensure-cdp для platform desktop healer.

    Контракт: ok|status|action|message. Эндпоинт никогда не падает 5xx; при
    недоступности browser-agent возвращает ok=false с пояснением.
    """

    ok: bool = True
    status: str = "UNKNOWN"  # READY | RECOVERED | UNAVAILABLE
    action: str = "none"  # none | restart
    message: str = ""

# -*- coding: utf-8 -*-
"""Pydantic-схемы для GET/PATCH /settings/observer."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ObserverSettingsResponse(BaseModel):
    """Текущая конфигурация observer без удалённых legacy-порогов."""

    model_config = ConfigDict(from_attributes=True)

    is_scanning_enabled: bool
    default_interval_seconds: int
    # Owner-scoping: тег владельца кампаний (NULL — фильтр выключен).
    owner_campaign_tag: str | None = None
    # Allowlist кампаний для am-режима (#3): фильтр am_tabular по campaign.id. Пусто — без фильтра.
    campaign_ids: list[str] = Field(default_factory=list)


class ObserverIntervalPatchRequest(BaseModel):
    """Точечное обновление интервала без перезаписи runtime-флагов."""

    default_interval_seconds: int = Field(
        ge=30,
        le=600,
        description="Интервал сканирования (30–600 секунд)",
    )


class ScanningToggleRequest(BaseModel):
    """Тело PATCH /settings/observer/scanning."""

    enabled: bool


class OwnerTagPatchRequest(BaseModel):
    """Тело PATCH /settings/observer/owner-tag — точечное обновление тега.

    Отдельный PATCH против лост-апдейта (аудит 2026-07-12, C-1): full-PUT из
    закэшированного клиентского состояния молча перезаписывал is_scanning_enabled.
    """

    owner_campaign_tag: str | None = Field(
        default=None,
        max_length=255,
        description="Теги владельца кампаний для owner-scoping. Один или несколько через "
        "запятую. Пусто/null — фильтр выключен.",
    )


class CampaignAllowlistRequest(BaseModel):
    """Тело PATCH /settings/observer/campaigns — allowlist кампаний для am-режима (#3)."""

    campaign_ids: list[str] = Field(
        default_factory=list,
        description="Список campaign.id для наблюдения. Пусто — без фильтра по кампаниям.",
    )


class ScanNowResponse(BaseModel):
    """Ответ на POST /settings/observer/scan-now."""

    status: Literal["queued"]
    task_id: int
    correlation_id: uuid.UUID


class CampaignOption(BaseModel):
    """Кампания для выбора в allowlist сканирования (#3)."""

    # Meta campaign.id (fb_campaign_id) — с ним сверяется am-фильтр.
    id: str
    name: str
    selected: bool

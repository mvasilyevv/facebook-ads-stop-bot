# -*- coding: utf-8 -*-
"""Pydantic-схемы для GET/PATCH /settings/observer."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.observer.am_columns import normalize_am_columns


class AdsManagerColumnOption(BaseModel):
    """Known presentation column exposed as a readable checkbox option."""

    id: str
    label: str


class ObserverSettingsResponse(BaseModel):
    """Текущая конфигурация observer без удалённых legacy-порогов."""

    model_config = ConfigDict(from_attributes=True)

    is_scanning_enabled: bool
    default_interval_seconds: int
    # Owner-scoping: тег владельца кампаний (NULL — фильтр выключен).
    owner_campaign_tag: str | None = None
    # Allowlist кампаний для am-режима (#3): фильтр am_tabular по campaign.id. Пусто — без фильтра.
    campaign_ids: list[str] = Field(default_factory=list)
    # Presentation-only columns of the human-visible Ads Manager tab. When
    # am_columns_use_default=true this is the built-in template shown by the UI;
    # browser-agent may still resolve its legacy env override before that template.
    am_columns: list[str]
    am_columns_use_default: bool
    am_column_options: list[AdsManagerColumnOption]


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


class AdsManagerColumnsPatchRequest(BaseModel):
    """Known presentation columns; null/empty resets runtime fallback."""

    column_ids: list[str] | None = Field(
        default=None,
        description=(
            "Известные колонки видимой вкладки Ads Manager. Пусто/null — "
            "использовать env BROWSER_AGENT_AM_COLUMNS_QS, затем встроенный default."
        ),
    )

    @field_validator("column_ids")
    @classmethod
    def validate_column_ids(cls, value: list[str] | None) -> list[str] | None:
        normalized = normalize_am_columns(value)
        return list(normalized) if normalized is not None else None


class ScanNowResponse(BaseModel):
    """Ответ на POST /settings/observer/scan-now."""

    status: Literal["queued", "running", "confirmed", "failed", "cancelled", "unknown"]
    task_id: int
    correlation_id: str
    created: bool


class CampaignOption(BaseModel):
    """Кампания для выбора в allowlist сканирования (#3)."""

    # Meta campaign.id (fb_campaign_id) — с ним сверяется am-фильтр.
    id: str
    name: str
    selected: bool

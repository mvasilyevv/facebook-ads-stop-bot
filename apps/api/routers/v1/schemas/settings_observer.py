# -*- coding: utf-8 -*-
"""Pydantic-схемы для роутера settings_observer (GET/PUT/PATCH /settings/observer).

Поля WARNING-параметров (*_percent_of_stop и т.п.) перенесены в OfferRule (per-offer).
Возвращаем null для этих полей, чтобы фронт получал стабильный shape.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ObserverSettingsResponse(BaseModel):
    """Ответ на GET /settings/observer.

    Поля, которые раньше были в глобальном observer_config, но теперь
    хранятся per-offer в OfferRule, возвращаются как null для совместимости
    с текущим фронтом.
    """

    model_config = ConfigDict(from_attributes=True)

    is_scanning_enabled: bool
    default_interval_seconds: int
    auto_enable_recommendations: bool
    # Owner-scoping: тег владельца кампаний (NULL — фильтр выключен).
    owner_campaign_tag: str | None = None
    # Allowlist кампаний для am-режима (#3): фильтр am_tabular по campaign.id. Пусто — без фильтра.
    campaign_ids: list[str] = Field(default_factory=list)

    # Поля, перенесённые в OfferRule: возвращаем null для стабильного shape.
    warning_percent_of_stop: None = None
    cpc_warning_percent: None = None
    cpl_warning_percent: None = None
    cpr_warning_percent: None = None


class ObserverSettingsPutRequest(BaseModel):
    """Тело PUT /settings/observer — обновление singleton.

    Валидация: default_interval_seconds должен быть от 30 до 600 секунд.
    """

    is_scanning_enabled: bool
    default_interval_seconds: int = Field(
        ge=30,
        le=600,
        description="Интервал сканирования (30–600 секунд)",
    )
    auto_enable_recommendations: bool
    owner_campaign_tag: str | None = Field(
        default=None,
        max_length=255,
        description="Теги владельца кампаний для owner-scoping. Один или несколько через "
        "запятую (например, 'MV' или 'MV,ABC,XYZ') — кампания отслеживается при совпадении "
        "с любым. Пусто/null — фильтр выключен, обрабатываются все кампании.",
    )
    # Allowlist кампаний для am-режима. None — НЕ трогаем; [] — очистить (без фильтра).
    campaign_ids: list[str] | None = Field(
        default=None,
        description="Allowlist кампаний для am-режима (#3): фильтр am_tabular по campaign.id IN. "
        "null — не менять, [] — очистить (без фильтра по кампаниям).",
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


class AutoEnableToggleRequest(BaseModel):
    """Тело PATCH /settings/observer/auto-enable."""

    enabled: bool


class CampaignAllowlistRequest(BaseModel):
    """Тело PATCH /settings/observer/campaigns — allowlist кампаний для am-режима (#3)."""

    campaign_ids: list[str] = Field(
        default_factory=list,
        description="Список campaign.id для наблюдения. Пусто — без фильтра по кампаниям.",
    )


class ScanNowResponse(BaseModel):
    """Ответ на POST /settings/observer/scan-now."""

    status: str


class CampaignOption(BaseModel):
    """Кампания для выбора в allowlist сканирования (#3)."""

    # Meta campaign.id (fb_campaign_id) — с ним сверяется am-фильтр.
    id: str
    name: str
    selected: bool

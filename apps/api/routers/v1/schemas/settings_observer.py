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
        max_length=64,
        description="Тег владельца кампаний для owner-scoping (например, 'MV'). "
        "Пусто/null — фильтр выключен, обрабатываются все кампании.",
    )


class ScanningToggleRequest(BaseModel):
    """Тело PATCH /settings/observer/scanning."""

    enabled: bool


class AutoEnableToggleRequest(BaseModel):
    """Тело PATCH /settings/observer/auto-enable."""

    enabled: bool


class ScanNowResponse(BaseModel):
    """Ответ на POST /settings/observer/scan-now."""

    status: str

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
    # Канал toggle-действий: False — DOM-клик, True — Marketing API (pause_ad/activate_ad).
    act_via_api: bool = False

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
    # Money-критичный флаг: None — НЕ трогаем (защита от сброса старыми клиентами,
    # которые не шлют поле). bool — явно выставить канал toggle-действий.
    act_via_api: bool | None = Field(
        default=None,
        description="Канал toggle-действий (disable/enable). False — DOM-клик browser-agent, "
        "True — Marketing API (pause_ad/activate_ad, точно по ad_id). null — не менять.",
    )


class ScanningToggleRequest(BaseModel):
    """Тело PATCH /settings/observer/scanning."""

    enabled: bool


class AutoEnableToggleRequest(BaseModel):
    """Тело PATCH /settings/observer/auto-enable."""

    enabled: bool


class ActViaApiToggleRequest(BaseModel):
    """Тело PATCH /settings/observer/act-via-api."""

    enabled: bool


class ScanNowResponse(BaseModel):
    """Ответ на POST /settings/observer/scan-now."""

    status: str

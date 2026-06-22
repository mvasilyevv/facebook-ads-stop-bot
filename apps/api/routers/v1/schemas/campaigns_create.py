# -*- coding: utf-8 -*-
"""Pydantic-схемы роутера campaigns_create (сервис создания FB-кампаний).

Контракт API↔воркер. CampaignConfig переиспользуется из core.campaign_builder
без форка — единый источник правды по структуре конфига.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from core.campaign_builder.config import CampaignConfig

# ────────────────────────────── presets ──────────────────────────────


class PresetIn(BaseModel):
    """Тело создания/обновления пресета (стабильный конфиг залива)."""

    name: str = Field(min_length=1, max_length=255)
    act_id: str = Field(min_length=1, max_length=64)
    page_id: str = Field(min_length=1, max_length=64)
    pixel_id: str = Field(min_length=1, max_length=64)
    tz_offset: int = 0
    offer_code: str | None = Field(default=None, max_length=64)
    byer_tag: str | None = Field(default=None, max_length=64)
    objective: str = "OUTCOME_SALES"
    optimization_goal: str = "OFFSITE_CONVERSIONS"
    custom_event_type: str = "PURCHASE"
    special_ad_categories: list[str] = Field(default_factory=lambda: ["NONE"])
    cta: str = "PLAY_GAME"
    text_optimizations: str = "OPT_OUT"
    click_through_days: int = 1
    view_through_days: int = 1
    url_tags_template: str | None = Field(default=None, max_length=1024)
    naming_template: str | None = Field(default=None, max_length=512)
    extra: dict[str, Any] = Field(default_factory=dict)


class PresetOut(BaseModel):
    """Пресет в ответе API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    act_id: str
    page_id: str
    pixel_id: str
    tz_offset: int
    offer_code: str | None
    byer_tag: str | None
    objective: str
    optimization_goal: str
    custom_event_type: str
    special_ad_categories: list[str]
    cta: str
    text_optimizations: str
    click_through_days: int
    view_through_days: int
    url_tags_template: str | None
    naming_template: str | None
    extra: dict[str, Any]
    created_at: str
    updated_at: str


# ────────────────────────────── upload ──────────────────────────────


class UploadedConceptOut(BaseModel):
    """Метаданные одного загруженного концепта (превью для UI)."""

    ref: str  # относительный путь внутри run-папки (имя файла)
    original_name: str
    size_bytes: int
    content_type: str | None = None


class UploadConceptsOut(BaseModel):
    """Ответ загрузки концептов: id временной папки + список файлов."""

    upload_id: str  # uuid временной папки (вход в config.creo_root воркера)
    upload_dir: str  # абсолютный путь к папке на сервере
    concepts: list[UploadedConceptOut]
    total_bytes: int


# ────────────────────────────── validate ──────────────────────────────


class ValidateIn(BaseModel):
    """Запрос dry-run валидации конфига."""

    config: CampaignConfig


class AdsetPlanOut(BaseModel):
    """Сводка по одному adset в плане."""

    name: str
    status: str
    ad_count: int


class CampaignPlanOut(BaseModel):
    """Сводка по одной кампании в плане."""

    key: str
    name: str
    kind: str
    status: str
    adsets: list[AdsetPlanOut]


class ValidatePlanOut(BaseModel):
    """Результат validate: число объектов + нейминг без создания."""

    offer_code: str
    launch_state: str
    copies_per_concept: int
    campaign_count: int
    adset_count: int
    ad_count: int
    campaigns: list[CampaignPlanOut]


# ────────────────────────────── launch ──────────────────────────────


class LaunchIn(BaseModel):
    """Запрос запуска залива: конфиг + опц. ссылка на пресет/upload."""

    config: CampaignConfig
    preset_id: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


class LaunchOut(BaseModel):
    """Ответ запуска: id созданного run + id задачи."""

    run_id: str
    task_id: int | None
    status: str
    idempotency_key: str


# ────────────────────────────── runs ──────────────────────────────


class RunSummaryOut(BaseModel):
    """Краткая карточка запуска для списка."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    preset_id: str | None
    status: str
    offer_code: str | None
    idempotency_key: str | None
    error: str | None
    created_at: str
    updated_at: str


class RunDetailOut(BaseModel):
    """Детали запуска: конфиг-снимок + прогресс + Meta-ID + ошибка."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    preset_id: str | None
    status: str
    config: dict[str, Any]
    progress: dict[str, Any]
    created_meta_ids: dict[str, Any]
    error: str | None
    idempotency_key: str | None
    created_at: str
    updated_at: str


class CleanupOut(BaseModel):
    """Результат пометки run на снос Meta-объектов."""

    run_id: str
    meta_ids: dict[str, Any]  # созданные id для ручного/задачного сноса
    detail: str

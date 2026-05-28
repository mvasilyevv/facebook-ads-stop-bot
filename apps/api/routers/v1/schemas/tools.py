# -*- coding: utf-8 -*-
"""Pydantic-схемы для Tools-эндпоинтов: уникализация креативов + campaign-create."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CreativeUniquifyResponse(BaseModel):
    """Результат уникализации: куда сохранено и сколько файлов создано."""

    model_config = ConfigDict(from_attributes=False)

    output_dir: str
    iteration_name: str
    files_created: int
    creative_count: int
    copy_count: int
    duration_ms: int


class OpenFolderRequest(BaseModel):
    """Запрос открытия папки результата."""

    path: str


class CampaignFolderItem(BaseModel):
    """Краткое описание одной папки креативов для UI выбора."""

    model_config = ConfigDict(from_attributes=False)

    name: str
    path: str
    adset_count: int
    creative_count: int
    media_type: str
    updated_at: float
    is_valid: bool = True
    validation_error: str = ""


class CampaignPlanRequest(BaseModel):
    """Параметры создания кампании — совпадают с CampaignScriptConfig."""

    offer_code: str = Field(..., description="Код оффера, например DRC_CR2")
    offer_country_name: str = Field(..., description="Полное название страны оффера")
    cabinet_id: str = Field(..., description="ID рекламного кабинета Facebook")
    sub2: str = Field(default="MV", description="Значение sub2 для UTM-параметров")
    folder_name: str = Field(..., description="Имя папки в корне FB_Agent_Creo")
    generation_date: str | None = Field(
        default=None,
        description="Дата генерации в формате YYYY-MM-DD (иначе сегодня)",
    )


class CampaignAdPlanOut(BaseModel):
    """План одного объявления."""

    model_config = ConfigDict(from_attributes=False)

    name: str
    media_file_name: str
    media_search_name: str
    media_path: str
    media_type: str
    url_params: str


class CampaignAdSetPlanOut(BaseModel):
    """План одной группы объявлений."""

    model_config = ConfigDict(from_attributes=False)

    name: str
    folder_path: str
    ads: list[CampaignAdPlanOut]


class CampaignLocationPlanOut(BaseModel):
    """Правила выбора гео."""

    model_config = ConfigDict(from_attributes=False)

    add_locations: list[str]
    offer_country_name: str
    required_location_type: str
    remove_initial_location_after_add: bool
    rejected_location_terms: list[str]


class CampaignManualGuideItemOut(BaseModel):
    """Одна строка ручного помощника."""

    model_config = ConfigDict(from_attributes=False)

    label: str
    value: str
    copyable: bool = True


class CampaignManualGuideSectionOut(BaseModel):
    """Секция ручного помощника."""

    model_config = ConfigDict(from_attributes=False)

    title: str
    items: list[CampaignManualGuideItemOut]


class CampaignScriptPlanOut(BaseModel):
    """Полный план кампании для ручного создания."""

    model_config = ConfigDict(from_attributes=False)

    campaign_name: str
    offer_code: str
    offer_country_name: str
    creative_folder_name: str
    creative_folder_path: str
    conversion_event: str
    cabinet_id: str
    sub2: str
    media_type: str
    adset_count: int
    ad_count: int
    adsets: list[CampaignAdSetPlanOut]
    location_plan: CampaignLocationPlanOut
    manual_guide: list[CampaignManualGuideSectionOut]
    safety_notes: list[str]

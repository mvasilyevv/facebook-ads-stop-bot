# -*- coding: utf-8 -*-
"""Pydantic-контракт API быстрого дублирования adset."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.adset_duplicates.service import (
    MAX_ADSETS_PER_CAMPAIGN,
    MAX_CAMPAIGN_COUNT,
    MAX_SELECTED_ADS,
    MAX_TOTAL_ADS,
)
from core.meta_api.mutations.set_adset_budget import MAX_DAILY_BUDGET_CENTS

NumericMetaId = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^\d+$")]


class AdsetDuplicatePreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_ad_id: NumericMetaId
    selected_ad_ids: list[NumericMetaId] = Field(min_length=1, max_length=MAX_SELECTED_ADS)
    campaign_count: int = Field(ge=1, le=MAX_CAMPAIGN_COUNT)
    adsets_per_campaign: int = Field(ge=1, le=MAX_ADSETS_PER_CAMPAIGN)
    budget_level: Literal["ABO", "CBO"]
    daily_budget_cents: int = Field(ge=1, le=MAX_DAILY_BUDGET_CENTS)
    start_date: date | None = None
    campaign_name_base: str | None = Field(default=None, min_length=1, max_length=300)
    adset_name_base: str | None = Field(default=None, min_length=1, max_length=300)
    idempotency_token: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )

    @field_validator("budget_level", mode="before")
    @classmethod
    def normalize_budget_level(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_selected_ads_and_total(self) -> "AdsetDuplicatePreviewIn":
        if len(set(self.selected_ad_ids)) != len(self.selected_ad_ids):
            raise ValueError("selected_ad_ids не должен содержать дубли")
        total_ads = self.campaign_count * self.adsets_per_campaign * len(self.selected_ad_ids)
        if total_ads > MAX_TOTAL_ADS:
            raise ValueError(f"Структура создаёт {total_ads} объявлений; максимум {MAX_TOTAL_ADS}")
        return self


class DuplicateSourceEntity(BaseModel):
    id: str
    name: str


class DuplicateSourceAccount(DuplicateSourceEntity):
    currency: str | None = None


class DuplicateSourceAd(DuplicateSourceEntity):
    fb_ad_id: str
    delivery_status: str | None = None
    creative_thumb_url: str | None = None


class AdsetDuplicateSource(BaseModel):
    account: DuplicateSourceAccount
    campaign: DuplicateSourceEntity
    adset: DuplicateSourceEntity
    ads: list[DuplicateSourceAd]


class AdsetDuplicateCounts(BaseModel):
    campaigns: int
    adsets: int
    ads: int
    total_objects: int


class AdsetDuplicateBudget(BaseModel):
    level: Literal["ABO", "CBO"]
    unit_daily_budget_cents: int
    total_daily_budget_cents: int
    currency: str


class AdsetDuplicateSchedule(BaseModel):
    timezone_name: str
    offset: str
    start_time_utc: str
    start_time_local: str


class AdsetDuplicateGeneratedNames(BaseModel):
    campaigns: list[str]
    adsets: list[str]


class AdsetDuplicatePreviewOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_token: str
    source: AdsetDuplicateSource
    format_code: str
    counts: AdsetDuplicateCounts
    budget: AdsetDuplicateBudget
    schedule: AdsetDuplicateSchedule
    generated_names: AdsetDuplicateGeneratedNames
    warnings: list[str]
    expires_at: datetime


class AdsetDuplicateDraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    preview_token: str = Field(min_length=20, max_length=128)


class AdsetDuplicateDraftOut(BaseModel):
    task_id: int
    status: str
    expires_at: datetime


class AdsetDuplicateProgress(BaseModel):
    model_config = ConfigDict(extra="allow")

    phase: str | None = None
    completed: int | None = None
    total: int | None = None
    message: str | None = None


class AdsetDuplicateStatusOut(BaseModel):
    task_id: int
    status: str
    progress: AdsetDuplicateProgress | None
    created_meta_ids: dict[str, str | list[str]] = Field(default_factory=dict)
    error: str | None
    expires_at: datetime | None = None


__all__ = [
    "AdsetDuplicateDraftIn",
    "AdsetDuplicateDraftOut",
    "AdsetDuplicatePreviewIn",
    "AdsetDuplicatePreviewOut",
    "AdsetDuplicateStatusOut",
]

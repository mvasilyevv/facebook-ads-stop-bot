# -*- coding: utf-8 -*-
"""Реестр шагов создания кампании — единый источник истины о порядке и составе."""

from __future__ import annotations

from .base import BaseStep
from .click_next import ClickNextStep, ClickNextToAdStep
from .create_adset import CreateAdsetStep
from .create_campaign import CreateCampaignStep
from .fill_texts import FillTextsStep
from .save_draft import SaveDraftStep
from .set_age import SetAgeStep
from .set_attribution import SetAttributionStep
from .set_budget import SetBudgetStep
from .set_conversion_location import SetConversionLocationStep
from .set_cta import SetCtaStep
from .set_geo import SetGeoStep
from .set_pixel_event import SetPixelEventStep
from .set_schedule_start import SetScheduleStartStep
from .set_tracking_url import SetTrackingUrlStep
from .upload_creatives import UploadCreativesStep

_STEP_CLASSES: list[type[BaseStep]] = [
    CreateCampaignStep,
    SetBudgetStep,
    ClickNextStep,
    CreateAdsetStep,
    SetConversionLocationStep,
    SetPixelEventStep,
    SetAttributionStep,
    SetScheduleStartStep,
    SetGeoStep,
    SetAgeStep,
    ClickNextToAdStep,
    UploadCreativesStep,
    FillTextsStep,
    SetCtaStep,
    SetTrackingUrlStep,
    SaveDraftStep,
]

STEPS_ORDER: list[str] = [cls.name for cls in _STEP_CLASSES]

_REGISTRY: dict[str, type[BaseStep]] = {cls.name: cls for cls in _STEP_CLASSES}

# В реестре уникальность гарантируется — каждый шаг с собственным name.
assert len(_REGISTRY) == len(_STEP_CLASSES), "Дублирующиеся имена шагов в registry"


def build_step(name: str) -> BaseStep:
    """Создать инстанс шага по его имени."""
    cls = _REGISTRY.get(name)
    if cls is None:
        raise KeyError(f"Неизвестный шаг: {name!r}. Доступные: {STEPS_ORDER}")
    return cls()


def build_pipeline(start_from: str | None = None) -> list[BaseStep]:
    """Собрать список инстансов шагов от start_from до конца включительно.

    start_from=None — полный пайплайн.
    """
    if start_from is None:
        return [cls() for cls in _STEP_CLASSES]
    if start_from not in _REGISTRY:
        raise KeyError(f"Неизвестный шаг: {start_from!r}. Доступные: {STEPS_ORDER}")
    idx = STEPS_ORDER.index(start_from)
    return [cls() for cls in _STEP_CLASSES[idx:]]


def step_idempotent(name: str) -> bool:
    """Признак идемпотентности шага (для подсказок в UI)."""
    cls = _REGISTRY.get(name)
    if cls is None:
        return False
    return getattr(cls, "idempotent", False)

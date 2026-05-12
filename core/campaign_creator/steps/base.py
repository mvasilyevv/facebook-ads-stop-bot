# -*- coding: utf-8 -*-
"""Базовый класс шага создания кампании."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

from playwright.async_api import Page


@dataclass
class AdsetSpec:
    """Спецификация одного адсета: имя, тексты, подпапка с креативами."""

    name: str
    headline: str
    primary_text: str
    creo_subfolder: str


@dataclass
class StepContext:
    """Контекст выполнения шага — общие данные для всего прогона."""

    offer_code: str
    cabinet_id: str
    campaign_name: str
    pixel_id: str
    landing_url: str
    geo_code: str
    geo_slot_name: str
    daily_budget: float
    attribution_days: Literal[1, 7]
    budget_level: Literal["CBO", "ABO"]
    iter_num: int
    adsets: list[AdsetSpec]
    creo_folder: str
    extra: dict = field(default_factory=dict)


@dataclass
class StepResult:
    """Результат выполнения шага."""

    success: bool
    message: str
    checkpoint_data: dict | None = None


class BaseStep(ABC):
    """Базовый класс шага создания кампании."""

    name: str = "base"
    is_checkpoint: bool = False

    @abstractmethod
    async def execute(self, page: Page, context: StepContext) -> StepResult:
        """Выполнить шаг в браузере."""
        ...

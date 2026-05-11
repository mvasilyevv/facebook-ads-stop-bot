# -*- coding: utf-8 -*-
"""Базовый класс шага создания кампании."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from playwright.async_api import Page


@dataclass
class StepContext:
    """Контекст выполнения шага — общие данные для всего прогона."""

    offer_code: str
    creative_folder: str
    cabinet_id: str
    campaign_name: str
    cdp_url: str
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

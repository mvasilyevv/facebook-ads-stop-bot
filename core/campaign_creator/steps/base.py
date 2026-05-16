# -*- coding: utf-8 -*-
"""Базовый класс шага создания кампании."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

from playwright.async_api import Page


@dataclass
class AdsetSpec:
    """Опциональный суффикс к имени адсета и тексты.

    Имя адсета и подпапка с креативами выводятся из позиции в списке.
    """

    name_suffix: str = ""
    headline: str = ""
    primary_text: str = ""

    def display_name(self, idx: int) -> str:
        """Имя адсета: '{N}' или '{N} | {suffix}'."""
        n = idx + 1
        suffix = (self.name_suffix or "").strip()
        return f"{n} | {suffix}" if suffix else f"{n}"

    def subfolder(self, idx: int) -> str:
        """Подпапка с креативами по индексу: '1', '2', ..."""
        return str(idx + 1)


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
    # Можно ли безопасно перезапускать на той же странице (для подсказки UI при resume).
    idempotent: bool = False

    @abstractmethod
    async def execute(
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> StepResult:
        """Выполнить шаг в браузере. params — для декларативного плана; None для legacy-runner."""
        ...

    async def pre_check(  # noqa: B027 — намеренно no-op, переопределяется в подклассах
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> None:
        """Проверка готовности UI к выполнению шага.

        Контракт: убедиться, что нужный drawer/уровень/секция активны. Если
        нет — поднять исключение со внятным сообщением. Без авто-исправления
        состояния — это задача execute. По умолчанию no-op.
        """

    async def verify(  # noqa: B027 — намеренно no-op, переопределяется в подклассах
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> None:
        """Пост-проверка: результат execute виден в DOM.

        Бросает исключение, если результат не подтвердился. По умолчанию no-op —
        многие шаги уже валидируют успех внутри execute.
        """

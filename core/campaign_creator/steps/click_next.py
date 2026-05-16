# -*- coding: utf-8 -*-
"""Шаг: нажать кнопку 'Далее' для перехода между уровнями (кампания → адсет → объявление)."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_click_label, human_wait

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)


class ClickNextStep(BaseStep):
    """Нажать 'Далее' — переход с уровня кампании на уровень адсета."""

    name = "click_next"
    is_checkpoint = False
    idempotent = False

    async def execute(
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> StepResult:
        try:
            await human_click_label(page, "Далее")
            await human_wait(1500, 2500)
            logger.info("Кнопка 'Далее' нажата")
            return StepResult(success=True, message="Перешли на следующий уровень")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка click_next: {exc}")


class ClickNextToAdStep(BaseStep):
    """Нажать 'Далее' — переход с уровня адсета на уровень объявления."""

    name = "click_next_to_ad"
    is_checkpoint = False
    idempotent = False

    async def execute(
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> StepResult:
        try:
            await human_click_label(page, "Далее")
            await human_wait(1500, 2500)
            logger.info("Кнопка 'Далее' нажата (адсет → объявление)")
            return StepResult(success=True, message="Перешли на уровень объявления")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка click_next_to_ad: {exc}")

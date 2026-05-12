# -*- coding: utf-8 -*-
"""Шаг: окно атрибуции (1 день / 7 дней по клику)."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_click, human_wait

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)


class SetAttributionStep(BaseStep):
    """Выбрать окно атрибуции 1d или 7d по клику."""

    name = "set_attribution"
    is_checkpoint = False

    async def execute(self, page: Page, context: StepContext) -> StepResult:
        try:
            await human_click(page, '[aria-label="Окно атрибуции"]')
            await human_wait(300, 600)
            label = "7 дней по клику" if context.attribution_days == 7 else "1 день по клику"
            await human_click(page, f'[role="option"]:has-text("{label}")')
            logger.info("Атрибуция: %s", label)
            return StepResult(success=True, message=f"Атрибуция: {label}")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка set_attribution: {exc}")

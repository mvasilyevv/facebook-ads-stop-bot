# -*- coding: utf-8 -*-
"""Финальный шаг: 'Сохранить как черновик'. Дальше — ручная проверка."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_click, human_wait

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)


class SaveDraftStep(BaseStep):
    """Нажать 'Сохранить как черновик' и остановиться."""

    name = "save_draft"
    is_checkpoint = False

    async def execute(self, page: Page, context: StepContext) -> StepResult:
        try:
            await human_click(page, '[aria-label="Сохранить как черновик"]')
            await human_wait(1000, 2000)
            logger.info("Черновик сохранён")
            return StepResult(success=True, message="Черновик сохранён")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка save_draft: {exc}")

# -*- coding: utf-8 -*-
"""Шаг: создать адсеты — итерация по ctx.adsets, задаём имя."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_click, human_type, human_wait
from core.campaign_creator.selectors import SELECTORS

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)


class CreateAdsetStep(BaseStep):
    """Для каждого AdsetSpec — создать адсет и ввести имя."""

    name = "create_adset"
    is_checkpoint = False

    async def execute(self, page: Page, context: StepContext) -> StepResult:
        try:
            for idx, adset in enumerate(context.adsets):
                if idx > 0:
                    await human_click(page, '[aria-label="Создать группу объявлений"]')
                    await human_wait(400, 800)
                await human_type(page, SELECTORS["adset_name"], adset.name)
                logger.info("Адсет %d: %s", idx + 1, adset.name)
            return StepResult(success=True, message=f"Создано адсетов: {len(context.adsets)}")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка create_adset: {exc}")

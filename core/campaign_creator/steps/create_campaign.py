# -*- coding: utf-8 -*-
"""Шаг: создать новую кампанию в Ads Manager (humanizer + selectors)."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_click, human_type, human_wait
from core.campaign_creator.selectors import SELECTORS

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)


class CreateCampaignStep(BaseStep):
    """Создать → Продажи → Продолжить → ввод названия кампании."""

    name = "create_campaign"
    is_checkpoint = False

    async def execute(self, page: Page, context: StepContext) -> StepResult:
        try:
            await human_click(page, '[aria-label="Создать"]')
            await human_wait(400, 800)
            await human_click(page, '[aria-label="Продажи"]')
            await human_click(page, '[aria-label="Продолжить"]')
            await human_type(page, SELECTORS["campaign_name"], context.campaign_name)
            logger.info("Кампания создана: %s", context.campaign_name)
            return StepResult(success=True, message=f"Кампания создана: {context.campaign_name}")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка create_campaign: {exc}")

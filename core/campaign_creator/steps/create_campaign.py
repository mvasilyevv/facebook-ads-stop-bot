# -*- coding: utf-8 -*-
"""Шаг: создать новую кампанию в Ads Manager."""
from __future__ import annotations

import logging

from playwright.async_api import Page

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)

# TODO: заполнить после анализа записи сессии
_BTN_CREATE_CAMPAIGN = "[aria-label='Создать кампанию']"
_INPUT_CAMPAIGN_NAME = "[aria-label='Название кампании']"
_BTN_CONVERSION = "[aria-label='Конверсии']"


class CreateCampaignStep(BaseStep):
    """Нажать 'Создать' → выбрать 'Конверсии' → ввести название кампании."""

    name = "create_campaign"
    is_checkpoint = True

    async def execute(self, page: Page, context: StepContext) -> StepResult:
        try:
            await page.click(_BTN_CREATE_CAMPAIGN, timeout=10_000)
            logger.info("Нажали 'Создать кампанию'")
            await page.click(_BTN_CONVERSION, timeout=10_000)
            logger.info("Выбрали цель 'Конверсии'")
            await page.fill(_INPUT_CAMPAIGN_NAME, context.campaign_name)
            logger.info("Ввели название кампании: %s", context.campaign_name)
            return StepResult(
                success=True,
                message=f"Кампания создана: {context.campaign_name}",
                checkpoint_data={"campaign_name": context.campaign_name},
            )
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка шага create_campaign: {exc}")

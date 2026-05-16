# -*- coding: utf-8 -*-
"""Шаг: создать новую кампанию в Ads Manager (humanizer + selectors)."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_click_label, human_type, human_wait
from core.campaign_creator.selectors import SELECTORS

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)


class CreateCampaignStep(BaseStep):
    """Создать → Продажи → Продолжить → ввод названия кампании."""

    name = "create_campaign"
    is_checkpoint = False

    async def execute(
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> StepResult:
        try:
            campaign_name = (params or {}).get("campaign_name", context.campaign_name)
            used = await human_click_label(page, "Создать")
            logger.info("Кнопка 'Создать' найдена: %s", used)
            await human_wait(500, 900)

            used = await human_click_label(page, "Продажи")
            logger.info("Цель 'Продажи' выбрана: %s", used)
            await human_wait(300, 600)

            used = await human_click_label(page, "Продолжить")
            logger.info("Кнопка 'Продолжить' нажата: %s", used)
            await human_wait(500, 900)

            await human_type(page, SELECTORS["campaign_name"], campaign_name)
            logger.info("Кампания создана: %s", campaign_name)
            return StepResult(success=True, message=f"Кампания создана: {campaign_name}")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка create_campaign: {exc}")

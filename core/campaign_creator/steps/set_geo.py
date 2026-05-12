# -*- coding: utf-8 -*-
"""Шаг: гео — целевая страна оффера + Антарктида."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_type, human_wait
from core.campaign_creator.selectors import SELECTORS

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)

ALWAYS_ADD_ANTARCTICA = True
ANTARCTICA = "Антарктида"


class SetGeoStep(BaseStep):
    """Ввести geo_slot_name → подтвердить, затем добавить Антарктиду."""

    name = "set_geo"
    is_checkpoint = False

    async def execute(self, page: Page, context: StepContext) -> StepResult:
        try:
            slot = context.geo_slot_name or context.extra.get("offer_country_name", "")
            if not slot:
                return StepResult(success=False, message="Нет geo_slot_name у оффера")

            await human_type(page, SELECTORS["geo_search"], slot)
            await human_wait(500, 900)
            await page.click(f'[role="option"]:has-text("{slot}")')
            logger.info("Гео добавлено: %s", slot)

            if ALWAYS_ADD_ANTARCTICA:
                await human_type(page, SELECTORS["geo_search"], ANTARCTICA)
                await human_wait(500, 900)
                await page.click(f'[role="option"]:has-text("{ANTARCTICA}")')
                logger.info("Гео добавлено: %s", ANTARCTICA)

            return StepResult(success=True, message=f"Гео: {slot} + {ANTARCTICA}")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка set_geo: {exc}")

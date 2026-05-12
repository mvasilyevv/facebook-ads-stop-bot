# -*- coding: utf-8 -*-
"""Шаг: выбрать Pixel и событие 'Покупка'."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_click, human_wait
from core.campaign_creator.selectors import SELECTORS

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)

EVENT = "Покупка"


class SetPixelEventStep(BaseStep):
    """Открыть селектор пикселя, выбрать по pixel_id, выбрать событие 'Покупка'."""

    name = "set_pixel_event"
    is_checkpoint = False

    async def execute(self, page: Page, context: StepContext) -> StepResult:
        try:
            await human_click(page, SELECTORS["pixel"])
            await human_wait(300, 700)
            await human_click(page, f'[role="option"]:has-text("{context.pixel_id}")')
            await human_wait(200, 500)
            await human_click(page, '[aria-label="Событие конверсии"]')
            await human_wait(200, 500)
            await human_click(page, f'[role="option"]:has-text("{EVENT}")')
            logger.info("Pixel %s, событие %s", context.pixel_id, EVENT)
            return StepResult(success=True, message=f"Pixel {context.pixel_id}, событие {EVENT}")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка set_pixel_event: {exc}")

# -*- coding: utf-8 -*-
"""Шаг: установить CTA = 'Играть'."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_click, human_wait

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)

CTA = "Играть"


class SetCtaStep(BaseStep):
    """Выбрать кнопку призыва к действию: Играть."""

    name = "set_cta"
    is_checkpoint = False

    async def execute(self, page: Page, context: StepContext) -> StepResult:
        try:
            await human_click(page, '[aria-label="Призыв к действию"]')
            await human_wait(200, 500)
            await human_click(page, f'[role="option"]:has-text("{CTA}")')
            logger.info("CTA = %s", CTA)
            return StepResult(success=True, message=f"CTA: {CTA}")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка set_cta: {exc}")

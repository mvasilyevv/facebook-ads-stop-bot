# -*- coding: utf-8 -*-
"""Шаг: установить CTA = 'Играть'."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_click_label, human_pick_option, human_wait

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)

CTA = "Играть"


class SetCtaStep(BaseStep):
    """Выбрать кнопку призыва к действию: Играть."""

    name = "set_cta"
    is_checkpoint = False
    idempotent = True

    async def execute(
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> StepResult:
        try:
            cta_label = (params or {}).get("cta", CTA)
            await human_click_label(page, "Призыв к действию")
            await human_wait(200, 500)
            await human_pick_option(page, cta_label)
            logger.info("CTA = %s", cta_label)
            return StepResult(success=True, message=f"CTA: {cta_label}")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка set_cta: {exc}")

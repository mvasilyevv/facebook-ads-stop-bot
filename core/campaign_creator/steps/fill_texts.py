# -*- coding: utf-8 -*-
"""Шаг: для каждого объявления — заполнить headline и primary_text."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_type, human_wait
from core.campaign_creator.selectors import SELECTORS

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)

_PRIMARY_TEXT = '[aria-label="Основной текст"]'


class FillTextsStep(BaseStep):
    """Заполнить headline и primary_text для каждого объявления."""

    name = "fill_texts"
    is_checkpoint = False

    async def execute(self, page: Page, context: StepContext) -> StepResult:
        try:
            for adset in context.adsets:
                await human_type(page, _PRIMARY_TEXT, adset.primary_text)
                await human_wait(200, 400)
                await human_type(page, SELECTORS["headline"], adset.headline)
                logger.info("Тексты адсета %s заполнены", adset.name)
            return StepResult(success=True, message="Тексты заполнены")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка fill_texts: {exc}")

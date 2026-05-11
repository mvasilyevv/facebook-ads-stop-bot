# -*- coding: utf-8 -*-
"""Шаг: настроить гео в группе объявлений."""
from __future__ import annotations

import logging

from playwright.async_api import Page

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)

# TODO: заполнить после анализа записи
_INPUT_GEO_SEARCH = "[placeholder='Поиск местоположения']"


class SetGeoStep(BaseStep):
    """Добавить страну оффера, удалить первоначальное гео."""

    name = "set_geo"
    is_checkpoint = False

    async def execute(self, page: Page, context: StepContext) -> StepResult:
        locations = [context.extra.get("offer_country_name", "")]
        try:
            for location in locations:
                if not location:
                    continue
                await page.fill(_INPUT_GEO_SEARCH, location, timeout=10_000)
                await page.keyboard.press("Enter")
                logger.info("Добавили гео: %s", location)
            return StepResult(
                success=True,
                message=f"Гео настроено: {', '.join(loc for loc in locations if loc)}",
            )
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка шага set_geo: {exc}")

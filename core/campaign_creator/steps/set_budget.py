# -*- coding: utf-8 -*-
"""Шаг: установить уровень бюджета (CBO/ABO) и дневную сумму."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_click, human_type, human_wait
from core.campaign_creator.selectors import SELECTORS

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)


class SetBudgetStep(BaseStep):
    """Выбрать CBO/ABO и ввести дневной бюджет в USD."""

    name = "set_budget"
    is_checkpoint = False

    async def execute(self, page: Page, context: StepContext) -> StepResult:
        try:
            label = "Бюджет кампании" if context.budget_level == "CBO" else "Бюджет группы"
            await human_click(page, f'[aria-label="{label}"]')
            await human_wait(200, 500)
            await human_type(page, SELECTORS["daily_budget"], str(int(context.daily_budget)))
            logger.info("Бюджет %s = %s USD", context.budget_level, context.daily_budget)
            return StepResult(
                success=True,
                message=f"Бюджет {context.budget_level} {context.daily_budget} USD",
            )
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка set_budget: {exc}")

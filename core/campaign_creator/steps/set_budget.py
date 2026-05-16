# -*- coding: utf-8 -*-
"""Шаг: установить уровень бюджета (CBO/ABO) и дневную сумму."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_click_label, human_type, human_wait
from core.campaign_creator.selectors import SELECTORS

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)


class SetBudgetStep(BaseStep):
    """Выбрать CBO/ABO и ввести дневной бюджет в USD."""

    name = "set_budget"
    is_checkpoint = False

    async def execute(
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> StepResult:
        try:
            p = params or {}
            level = p.get("level", context.budget_level)
            daily_budget = p.get("daily_budget", context.daily_budget)
            label = "Бюджет кампании" if level == "CBO" else "Бюджет группы"
            await human_click_label(page, label)
            await human_wait(200, 500)
            amount = float(daily_budget)
            if amount == int(amount):
                budget_text = str(int(amount))
            else:
                # FB в RU-локали ожидает запятую как десятичный разделитель.
                budget_text = f"{amount:.2f}".replace(".", ",")
            await human_type(page, SELECTORS["daily_budget"], budget_text)
            logger.info("Бюджет %s = %s USD", level, daily_budget)
            return StepResult(
                success=True,
                message=f"Бюджет {level} {daily_budget} USD",
            )
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка set_budget: {exc}")

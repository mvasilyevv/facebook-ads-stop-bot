# -*- coding: utf-8 -*-
"""Runner шагов создания кампании — full autopilot без checkpoint-пауз."""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from playwright.async_api import Page

from core.campaign_creator.steps.base import BaseStep, StepContext, StepResult
from core.domain import CampaignCreatorTaskStatus

logger = logging.getLogger(__name__)


class CampaignCreatorRunner:
    """Выполняет список шагов до конца или до первой ошибки."""

    def __init__(
        self,
        steps: list[BaseStep],
        set_status: Callable[..., Awaitable[None]],
    ) -> None:
        self._steps = steps
        self._set_status = set_status

    async def run_all(self, page: Page, context: StepContext) -> bool:
        """Выполнить все шаги последовательно. True — успех, False — ошибка."""
        for idx, step in enumerate(self._steps, start=1):
            logger.info("Выполняю шаг %d/%d: %s", idx, len(self._steps), step.name)
            await self._set_status(CampaignCreatorTaskStatus.RUNNING, step=step.name)
            result: StepResult = await step.execute(page, context)
            if not result.success:
                await self._set_status(
                    CampaignCreatorTaskStatus.FAILED,
                    step=step.name,
                    data={"error": result.message},
                )
                logger.error("Шаг %s провалился: %s", step.name, result.message)
                return False
            logger.info("Шаг %s завершён: %s", step.name, result.message)

        await self._set_status(CampaignCreatorTaskStatus.SUCCEEDED)
        return True

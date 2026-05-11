# -*- coding: utf-8 -*-
"""Runner шагов создания кампании с checkpoint-паузами."""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from playwright.async_api import Page

from core.campaign_creator.steps.base import BaseStep, StepContext, StepResult
from core.domain import CampaignCreatorTaskStatus

logger = logging.getLogger(__name__)


class CampaignCreatorRunner:
    """Выполняет список шагов, останавливается на checkpoint для подтверждения."""

    def __init__(
        self,
        steps: list[BaseStep],
        set_status: Callable[..., Awaitable[None]],
    ) -> None:
        self._steps = steps
        self._set_status = set_status
        self._current_index = 0

    async def run_until_checkpoint(self, page: Page, context: StepContext) -> bool:
        """Выполняет шаги до первого checkpoint или до конца.

        Возвращает True если все шаги пройдены, False если ждёт подтверждения.
        """
        while self._current_index < len(self._steps):
            step = self._steps[self._current_index]
            logger.info(
                "Выполняю шаг %d/%d: %s",
                self._current_index + 1,
                len(self._steps),
                step.name,
            )

            await self._set_status(
                CampaignCreatorTaskStatus.RUNNING,
                step=step.name,
            )
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
            self._current_index += 1

            if step.is_checkpoint:
                await self._set_status(
                    CampaignCreatorTaskStatus.WAITING_CONFIRMATION,
                    step=step.name,
                    data=result.checkpoint_data,
                )
                logger.info("Checkpoint '%s' — ожидаю подтверждения", step.name)
                return False

        await self._set_status(CampaignCreatorTaskStatus.SUCCEEDED)
        return True

    def confirm_and_continue(self) -> None:
        """Сбрасывает паузу после подтверждения пользователем."""
        pass

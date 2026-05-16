# -*- coding: utf-8 -*-
"""Тонкая обёртка над step_executor — поддерживает legacy steps и декларативный plan."""

from __future__ import annotations

from playwright.async_api import Page

from core.campaign_creator.plan_types import PlanAction
from core.campaign_creator.step_executor import SetStatus, execute_plan, execute_steps
from core.campaign_creator.steps.base import BaseStep, StepContext


class CampaignCreatorRunner:
    """Выполняет либо linear pipeline (legacy), либо декларативный план."""

    def __init__(
        self,
        steps: list[BaseStep] | None = None,
        set_status: SetStatus = None,  # type: ignore[assignment]
        *,
        plan: list[PlanAction] | None = None,
    ) -> None:
        self._steps = steps
        self._plan = plan
        self._set_status = set_status

    async def run_all(
        self,
        page: Page,
        context: StepContext,
        *,
        state: dict | None = None,
    ) -> bool:
        if self._plan is not None:
            return await execute_plan(self._plan, page, context, self._set_status, state=state)
        if self._steps is None:
            raise ValueError("CampaignCreatorRunner: нужны либо plan, либо steps")
        return await execute_steps(self._steps, page, context, self._set_status)

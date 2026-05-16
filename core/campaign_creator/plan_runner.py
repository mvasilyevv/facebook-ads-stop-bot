# -*- coding: utf-8 -*-
"""PlanRunner — тонкий исполнитель списка PlanAction."""

from __future__ import annotations

import logging
from typing import Any, Callable, Protocol

from core.campaign_creator.plan_types import PlanAction
from core.campaign_creator.steps.base import StepResult

logger = logging.getLogger(__name__)


class _Step(Protocol):
    name: str

    async def execute(self, page, context, params=None) -> StepResult: ...

    async def pre_check(self, page, context, params=None) -> None: ...

    async def verify(self, page, context, params=None) -> None: ...


SetStatus = Callable[..., None]


class PlanRunner:
    def __init__(self, registry: dict[str, Callable[[], _Step]]):
        self._registry = registry

    async def run(
        self,
        page,
        ctx: Any,
        plan: list[PlanAction],
        state: dict,
        set_status: SetStatus,
    ) -> bool:
        start = state.get("progress_index", 0)
        for i in range(start, len(plan)):
            action = plan[i]
            set_status(i, action.step, "RUNNING")

            factory = self._registry.get(action.step)
            if factory is None:
                set_status(i, action.step, "FAILED", f"Неизвестный шаг {action.step!r}")
                return False

            try:
                step = factory()
                await self._run_hook(step, "pre_check", page, ctx, action.params)
                result: StepResult = await step.execute(page, ctx, action.params)
                if result.success:
                    await self._run_hook(step, "verify", page, ctx, action.params)
            except Exception as exc:
                logger.exception("Шаг %s упал с исключением", action.step)
                set_status(i, action.step, "FAILED", f"{type(exc).__name__}: {exc}")
                return False

            if not result.success:
                set_status(i, action.step, "FAILED", result.message)
                return False

            state["progress_index"] = i + 1
            fb = state.get("fb_state")
            if fb is not None and hasattr(fb, "mark_done"):
                fb.mark_done(i)
            set_status(i, action.step, "SUCCEEDED", result.message)

        return True

    @staticmethod
    async def _run_hook(step, hook: str, page, ctx, params) -> None:
        """Вызвать pre_check/verify, если шаг их переопределил.

        Если хук — это унаследованный no-op из BaseStep, дополнительной работы
        не делаем. Это даёт совместимость со старыми шагами без обязательной
        миграции.
        """
        fn = getattr(step, hook, None)
        if fn is None:
            return
        await fn(page, ctx, params)

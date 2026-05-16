# -*- coding: utf-8 -*-
# Сценарий: BaseStep.execute принимает (page, ctx, params) и params доступны внутри
import asyncio

from core.campaign_creator.steps.base import BaseStep, StepResult


class _DummyStep(BaseStep):
    name = "dummy"

    async def execute(self, page, context, params=None):
        return StepResult(success=True, message=str((params or {}).get("k", "none")))


def test_dummy_passes_params():
    step = _DummyStep()
    result = asyncio.run(step.execute(None, None, {"k": "v"}))
    assert result.message == "v"


def test_dummy_without_params():
    step = _DummyStep()
    result = asyncio.run(step.execute(None, None))
    assert result.message == "none"

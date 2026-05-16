# -*- coding: utf-8 -*-
"""Интеграционный тест runner.run_all с моками Playwright."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.campaign_creator.runner import CampaignCreatorRunner
from core.campaign_creator.steps.base import AdsetSpec, BaseStep, StepContext, StepResult
from core.domain import CampaignCreatorTaskStatus


def _make_context() -> StepContext:
    return StepContext(
        offer_code="X",
        cabinet_id="act_1",
        campaign_name="CR1 | X | MV | 01.01",
        pixel_id="px",
        landing_url="https://example.com",
        geo_code="US",
        geo_slot_name="USA",
        daily_budget=10.0,
        attribution_days=7,
        budget_level="CBO",
        iter_num=1,
        adsets=[AdsetSpec(name_suffix="A1", headline="H", primary_text="T")],
        creo_folder="/tmp",
    )


class _OkStep(BaseStep):
    name = "ok"

    async def execute(self, page, context, params=None):
        return StepResult(success=True, message="ok")


class _FailStep(BaseStep):
    name = "fail"

    async def execute(self, page, context, params=None):
        return StepResult(success=False, message="boom")


# Сценарий: успешный прогон всех шагов выставляет SUCCEEDED.
@pytest.mark.asyncio
async def test_run_all_success_sets_succeeded():
    set_status = AsyncMock()
    runner = CampaignCreatorRunner(steps=[_OkStep(), _OkStep()], set_status=set_status)
    page = MagicMock()
    result = await runner.run_all(page, _make_context())
    assert result is True
    statuses = [call.args[0] for call in set_status.call_args_list]
    assert CampaignCreatorTaskStatus.SUCCEEDED in statuses
    assert statuses.count(CampaignCreatorTaskStatus.RUNNING) == 2


# Сценарий: первый упавший шаг останавливает прогон и выставляет FAILED.
@pytest.mark.asyncio
async def test_run_all_stops_on_failure():
    set_status = AsyncMock()
    fail_step = _FailStep()
    after_step = _OkStep()
    after_step.name = "after"
    runner = CampaignCreatorRunner(steps=[fail_step, after_step], set_status=set_status)
    page = MagicMock()
    result = await runner.run_all(page, _make_context())
    assert result is False
    statuses = [call.args[0] for call in set_status.call_args_list]
    assert CampaignCreatorTaskStatus.FAILED in statuses
    assert CampaignCreatorTaskStatus.SUCCEEDED not in statuses
    failed_call = next(
        c for c in set_status.call_args_list if c.args[0] == CampaignCreatorTaskStatus.FAILED
    )
    assert failed_call.kwargs["data"]["error"] == "boom"

# -*- coding: utf-8 -*-
"""Тесты для campaign_creator — шаги, runner, статусная машина."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock


# Проверяем базовый интерфейс Step
def test_base_step_has_required_methods():
    """Каждый Step должен иметь name, is_checkpoint, execute()."""
    from core.campaign_creator.steps.base import BaseStep
    assert hasattr(BaseStep, 'name')
    assert hasattr(BaseStep, 'is_checkpoint')
    assert hasattr(BaseStep, 'execute')


@pytest.mark.asyncio
async def test_runner_pauses_on_checkpoint():
    """Runner должен установить статус WAITING_CONFIRMATION на checkpoint-шаге."""
    from core.campaign_creator.runner import CampaignCreatorRunner
    from core.campaign_creator.steps.base import BaseStep, StepContext, StepResult

    class FakeCheckpointStep(BaseStep):
        name = "fake_checkpoint"
        is_checkpoint = True

        async def execute(self, page, context):
            return StepResult(success=True, message="ok")

    class FakeNormalStep(BaseStep):
        name = "fake_normal"
        is_checkpoint = False

        async def execute(self, page, context):
            return StepResult(success=True, message="ok")

    statuses = []

    async def mock_set_status(status, step=None, data=None):
        statuses.append(status)

    mock_page = AsyncMock()
    context = StepContext(
        offer_code="DRC_CR2",
        creative_folder="test",
        cabinet_id="123",
        campaign_name="MV | DRC",
        extra={},
    )
    runner = CampaignCreatorRunner(
        steps=[FakeNormalStep(), FakeCheckpointStep()],
        set_status=mock_set_status,
    )
    await runner.run_until_checkpoint(mock_page, context)
    assert "WAITING_CONFIRMATION" in statuses

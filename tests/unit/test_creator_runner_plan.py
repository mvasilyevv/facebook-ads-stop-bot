# -*- coding: utf-8 -*-
# Сценарий: CampaignCreatorRunner с plan вызывает execute_plan, со steps — execute_steps.
# Сценарий: успешный плановый прогон ставит SUCCEEDED, провальный — FAILED с last error.
import asyncio

from core.campaign_creator.plan_types import PlanAction
from core.campaign_creator.runner import CampaignCreatorRunner
from core.campaign_creator.steps.base import StepResult
from core.domain import CampaignCreatorTaskStatus


class _OkStep:
    name = "ok"

    async def execute(self, page, ctx, params=None):
        return StepResult(success=True, message="готово")


class _BadStep:
    name = "bad"

    async def execute(self, page, ctx, params=None):
        return StepResult(success=False, message="нет элемента")


def _collect_set_status():
    calls = []

    async def f(status, **kw):
        calls.append((status, kw))

    return f, calls


def test_runner_plan_success(monkeypatch):
    from core.campaign_creator import step_executor

    monkeypatch.setattr(step_executor, "STEP_REGISTRY", {"ok": _OkStep})
    plan = [PlanAction("ok"), PlanAction("ok")]
    set_status, calls = _collect_set_status()
    runner = CampaignCreatorRunner(plan=plan, set_status=set_status)
    ok = asyncio.run(runner.run_all(None, None))
    assert ok is True
    assert calls[-1][0] == CampaignCreatorTaskStatus.SUCCEEDED


def test_runner_plan_failure(monkeypatch):
    from core.campaign_creator import step_executor

    monkeypatch.setattr(step_executor, "STEP_REGISTRY", {"ok": _OkStep, "bad": _BadStep})
    plan = [PlanAction("ok"), PlanAction("bad")]
    set_status, calls = _collect_set_status()
    runner = CampaignCreatorRunner(plan=plan, set_status=set_status)
    ok = asyncio.run(runner.run_all(None, None))
    assert ok is False
    assert calls[-1][0] == CampaignCreatorTaskStatus.FAILED
    assert calls[-1][1].get("step") == "bad"
    assert "нет элемента" in (calls[-1][1].get("data", {}).get("error") or "")


def test_runner_steps_path_still_works():
    # Сценарий: легаси-путь через steps не сломан.
    async def noop_set_status(*a, **k):
        return None

    runner = CampaignCreatorRunner(steps=[], set_status=noop_set_status)
    ok = asyncio.run(runner.run_all(None, None))
    assert ok is True

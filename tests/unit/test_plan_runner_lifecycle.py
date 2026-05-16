# -*- coding: utf-8 -*-
"""PlanRunner-lifecycle: pre_check → execute → verify, в этом порядке."""

from __future__ import annotations

import pytest

from core.campaign_creator.plan_runner import PlanRunner
from core.campaign_creator.plan_types import PlanAction
from core.campaign_creator.steps.base import BaseStep, StepResult


class _RecordingStep(BaseStep):
    """Шаг, фиксирующий порядок вызовов хуков."""

    name = "rec"

    def __init__(self, calls: list[str], fail_execute: bool = False):
        self._calls = calls
        self._fail = fail_execute

    async def pre_check(self, page, ctx, params=None):
        self._calls.append("pre_check")

    async def execute(self, page, ctx, params=None):
        self._calls.append("execute")
        return StepResult(success=not self._fail, message="ok" if not self._fail else "boom")

    async def verify(self, page, ctx, params=None):
        self._calls.append("verify")


def _set_status_stub():
    def f(i, step, status, message=None):
        pass

    return f


# Удачный шаг: pre_check → execute → verify в строгом порядке.
@pytest.mark.asyncio
async def test_lifecycle_order_on_success():
    calls: list[str] = []
    registry = {"rec": lambda: _RecordingStep(calls)}
    runner = PlanRunner(registry)
    state: dict = {}
    ok = await runner.run(None, None, [PlanAction("rec")], state, _set_status_stub())
    assert ok is True
    assert calls == ["pre_check", "execute", "verify"]


# Если execute упал — verify НЕ вызывается.
@pytest.mark.asyncio
async def test_verify_skipped_on_execute_failure():
    calls: list[str] = []
    registry = {"rec": lambda: _RecordingStep(calls, fail_execute=True)}
    runner = PlanRunner(registry)
    state: dict = {}
    ok = await runner.run(None, None, [PlanAction("rec")], state, _set_status_stub())
    assert ok is False
    assert calls == ["pre_check", "execute"]


# Шаг без переопределения хуков (только execute) — работает как раньше.
@pytest.mark.asyncio
async def test_step_without_hooks_still_runs():
    calls: list[str] = []

    class _Legacy:
        name = "legacy"

        async def execute(self, page, ctx, params=None):
            calls.append("execute")
            return StepResult(success=True, message="ok")

    runner = PlanRunner({"legacy": _Legacy})
    state: dict = {}
    ok = await runner.run(None, None, [PlanAction("legacy")], state, _set_status_stub())
    assert ok is True
    assert calls == ["execute"]


# Исключение в pre_check фиксируется как FAILED, execute не вызывается.
@pytest.mark.asyncio
async def test_pre_check_exception_blocks_execute():
    calls: list[str] = []

    class _Step(BaseStep):
        name = "x"

        async def pre_check(self, page, ctx, params=None):
            calls.append("pre_check")
            raise RuntimeError("drawer не открыт")

        async def execute(self, page, ctx, params=None):
            calls.append("execute")
            return StepResult(success=True, message="ok")

    statuses: list[tuple] = []

    def status(i, step, st, message=None):
        statuses.append((step, st, message))

    runner = PlanRunner({"x": _Step})
    state: dict = {}
    ok = await runner.run(None, None, [PlanAction("x")], state, status)
    assert ok is False
    assert calls == ["pre_check"]
    assert statuses[-1][1] == "FAILED"
    assert "drawer" in (statuses[-1][2] or "")

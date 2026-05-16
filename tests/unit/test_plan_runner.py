# -*- coding: utf-8 -*-
# Сценарий: PlanRunner проходит план, увеличивает progress_index, отмечает done.
# Сценарий: при FAIL шаге останавливается, оставшиеся не выполняются.
# Сценарий: при exception в шаге останавливается, ошибка прокидывается через set_status.
import asyncio

from core.campaign_creator.plan_runner import PlanRunner
from core.campaign_creator.plan_types import FBState, PlanAction
from core.campaign_creator.steps.base import StepResult


class _FakeStep:
    name = "fake"

    def __init__(self, ok=True, raise_exc=False, message="ok"):
        self.ok = ok
        self.raise_exc = raise_exc
        self.message = message

    async def execute(self, page, ctx, params=None):
        if self.raise_exc:
            raise RuntimeError("boom")
        return StepResult(success=self.ok, message=self.message)


def _make_set_status():
    calls = []

    def set_status(idx, name, status, message=None):
        calls.append((idx, name, status, message))

    return set_status, calls


def test_runner_advances_index_and_marks_done():
    plan = [PlanAction("a"), PlanAction("a")]
    registry = {"a": lambda: _FakeStep(ok=True)}
    state = {"progress_index": 0, "fb_state": FBState()}
    set_status, calls = _make_set_status()
    ok = asyncio.run(PlanRunner(registry).run(None, None, plan, state, set_status))
    assert ok is True
    assert state["progress_index"] == 2
    assert state["fb_state"].is_done(0)
    assert state["fb_state"].is_done(1)
    assert [c[2] for c in calls] == ["RUNNING", "SUCCEEDED", "RUNNING", "SUCCEEDED"]


def test_runner_stops_on_failure():
    plan = [PlanAction("a"), PlanAction("b"), PlanAction("a")]
    registry = {
        "a": lambda: _FakeStep(ok=True),
        "b": lambda: _FakeStep(ok=False, message="нет кнопки"),
    }
    state = {"progress_index": 0, "fb_state": FBState()}
    set_status, calls = _make_set_status()
    ok = asyncio.run(PlanRunner(registry).run(None, None, plan, state, set_status))
    assert ok is False
    assert state["progress_index"] == 1
    assert calls[-1] == (1, "b", "FAILED", "нет кнопки")


def test_runner_handles_exception():
    plan = [PlanAction("boom")]
    registry = {"boom": lambda: _FakeStep(raise_exc=True)}
    state = {"progress_index": 0, "fb_state": FBState()}
    set_status, calls = _make_set_status()
    ok = asyncio.run(PlanRunner(registry).run(None, None, plan, state, set_status))
    assert ok is False
    assert state["progress_index"] == 0
    assert calls[-1][2] == "FAILED"
    assert "boom" in (calls[-1][3] or "")


def test_runner_resumes_from_progress_index():
    plan = [PlanAction("a"), PlanAction("a"), PlanAction("a")]
    registry = {"a": lambda: _FakeStep(ok=True)}
    state = {"progress_index": 2, "fb_state": FBState()}
    set_status, calls = _make_set_status()
    ok = asyncio.run(PlanRunner(registry).run(None, None, plan, state, set_status))
    assert ok is True
    assert state["progress_index"] == 3
    # Только один шаг выполнен — последний.
    assert [c[0] for c in calls] == [2, 2]


def test_runner_unknown_step():
    plan = [PlanAction("ghost")]
    registry = {}
    state = {"progress_index": 0, "fb_state": FBState()}
    set_status, calls = _make_set_status()
    ok = asyncio.run(PlanRunner(registry).run(None, None, plan, state, set_status))
    assert ok is False
    assert calls[-1][2] == "FAILED"

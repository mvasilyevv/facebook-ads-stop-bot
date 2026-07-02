# -*- coding: utf-8 -*-
"""Unit: H-3 из аудита — task_loop creator_worker не роняется крашем process_one_task.

Money-safety: plan_run исполняет реальный залив FB-кампании через Vision (необратимо).
До фикса вызов process_one_task в task_loop был БЕЗ try/except (в отличие от
apps/campaign_creator_worker) — неожиданный краш (напр. БД-сбой) ронял цикл, задача
оставалась в 'running', reconciler через 30 минут переводил её в 'retrying', и
повторное исполнение plan_run означало ДУБЛЬ залива кампании (двойной открут бюджета).

Здесь проверяем:
1. task_loop переживает исключение из process_one_task (цикл не падает).
2. При краше задача явно уводится в mark_failed (не остаётся молча висеть в 'running').
3. Если mark_failed ПОСЛЕ краша тоже падает — task_loop всё равно не роняется.
4. plan_run входит в IRREVERSIBLE_TASK_TYPES — reconciler не ретраит зависшую задачу.

SQL-поведение reconcile_stuck_running на реальном Postgres (зависший plan_run не
уходит в retrying) проверяется интеграционным тестом, здесь — только контракт набора.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from apps.creator_worker import main as worker_main
from core.tasks.queue import IRREVERSIBLE_TASK_TYPES, Task


def _fake_claimed_task() -> Task:
    return Task(
        id=777,
        task_type="plan_run",
        status="running",
        idempotency_key="test-key-loop",
        payload={"plan_id": "11111111-2222-3333-4444-555555555555"},
        attempt_count=0,
        max_attempts=5,
        requested_by="test",
    )


class _StopAfterOne:
    """asyncio.Event-подобная заглушка: is_set()=False один раз, потом True.

    Позволяет прогнать ровно одну итерацию task_loop без реального asyncio.Event
    и без сна — wait() бросает, чтобы task_loop не завис на настоящем таймауте.
    """

    def __init__(self) -> None:
        self._calls = 0

    def is_set(self) -> bool:
        self._calls += 1
        return self._calls > 1

    async def wait(self) -> None:
        # Не используется в сценарии "claim успешен → process_one_task" —
        # after одной итерации is_set() вернёт True и цикл завершится сам.
        raise AssertionError("wait() не должен вызываться в этом сценарии")


# Краш process_one_task (неожиданное исключение) не роняет task_loop, задача → mark_failed
@pytest.mark.asyncio
async def test_task_loop_survives_process_one_task_crash(monkeypatch) -> None:
    task = _fake_claimed_task()

    from core.tasks.queue import TaskClaim

    claim_mock = AsyncMock(return_value=TaskClaim(task=task, queue_empty=False))
    monkeypatch.setattr(worker_main, "claim_next_task", claim_mock)

    async def _boom(*_a, **_kw):
        raise RuntimeError("неожиданный сбой БД в process_one_task")

    monkeypatch.setattr(worker_main, "process_one_task", _boom)
    mark_failed_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(worker_main, "mark_failed", mark_failed_mock)

    stop = _StopAfterOne()
    # task_loop не должен выбросить исключение наружу — это и есть проверка "не роняет цикл".
    await worker_main.task_loop(engine=None, stop=stop, client=AsyncMock())

    mark_failed_mock.assert_awaited_once()
    assert mark_failed_mock.call_args.kwargs["task_id"] == task.id
    assert "crash" in mark_failed_mock.call_args.kwargs["error"]


# Даже если mark_failed после краша тоже падает — task_loop не роняется (двойная защитная сетка)
@pytest.mark.asyncio
async def test_task_loop_survives_mark_failed_failure_too(monkeypatch) -> None:
    task = _fake_claimed_task()

    from core.tasks.queue import TaskClaim

    claim_mock = AsyncMock(return_value=TaskClaim(task=task, queue_empty=False))
    monkeypatch.setattr(worker_main, "claim_next_task", claim_mock)

    async def _boom(*_a, **_kw):
        raise RuntimeError("сбой в process_one_task")

    async def _mark_failed_boom(*_a, **_kw):
        raise RuntimeError("БД недоступна даже для mark_failed")

    monkeypatch.setattr(worker_main, "process_one_task", _boom)
    monkeypatch.setattr(worker_main, "mark_failed", _mark_failed_boom)

    stop = _StopAfterOne()
    # Не должно бросить исключение — двойной except гасит и вторичный сбой.
    await worker_main.task_loop(engine=None, stop=stop, client=AsyncMock())


# Штатный успешный путь: process_one_task не падает → mark_failed НЕ вызывается из task_loop
@pytest.mark.asyncio
async def test_task_loop_happy_path_does_not_call_mark_failed(monkeypatch) -> None:
    task = _fake_claimed_task()

    from core.tasks.queue import TaskClaim

    claim_mock = AsyncMock(return_value=TaskClaim(task=task, queue_empty=False))
    monkeypatch.setattr(worker_main, "claim_next_task", claim_mock)

    process_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(worker_main, "process_one_task", process_mock)
    mark_failed_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(worker_main, "mark_failed", mark_failed_mock)

    stop = _StopAfterOne()
    await worker_main.task_loop(engine=None, stop=stop, client=AsyncMock())

    process_mock.assert_awaited_once()
    mark_failed_mock.assert_not_awaited()


# ====================== IRREVERSIBLE_TASK_TYPES контракт ======================


# plan_run — необратимая мутация (реальный залив кампании через Vision), не должен
# слепо ретраиться reconciler'ом наравне с обычными transient-задачами (disable/enable).
def test_plan_run_in_irreversible_task_types() -> None:
    assert "plan_run" in IRREVERSIBLE_TASK_TYPES


# campaign_create остаётся в наборе — добавление plan_run не вытеснило существующий контракт.
def test_campaign_create_still_in_irreversible_task_types() -> None:
    assert "campaign_create" in IRREVERSIBLE_TASK_TYPES


# reconcile_stuck_running строит SQL с безусловным guard, включающим оба необратимых типа.
@pytest.mark.asyncio
async def test_reconcile_excludes_plan_run_unconditionally(monkeypatch) -> None:
    from core.tasks.queue import reconcile_stuck_running

    captured: dict = {}

    class _FakeResult:
        rowcount = 0

    class _FakeConn:
        async def execute(self, stmt, params):
            captured["sql"] = str(stmt)
            captured["params"] = params
            return _FakeResult()

    class _FakeBegin:
        async def __aenter__(self):
            return _FakeConn()

        async def __aexit__(self, *a):
            return False

    class _FakeEngine:
        def begin(self):
            return _FakeBegin()

    await reconcile_stuck_running(_FakeEngine(), exclude_kinds=None)

    sql = captured["sql"]
    assert "task_type NOT IN" in sql
    assert "plan_run" in captured["params"]["irrev_types"]
    assert "campaign_create" in captured["params"]["irrev_types"]

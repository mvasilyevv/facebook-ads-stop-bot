# -*- coding: utf-8 -*-
"""Unit-тесты creator_worker: парсинг payload, классификация ошибок, обработка stream'а.

Без БД и реального gRPC — только чистая логика и моки.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import grpc
import pytest

from apps.creator_worker import main as worker_main
from apps.creator_worker.main import (
    _PERMANENT_EXCEPTIONS,
    _TEMPORARY_EXCEPTIONS,
    _execute_plan_stream,
    _parse_plan_id,
    process_one_task,
)
from clients.python_grpc.client import BrowserUnavailableError
from core.tasks.queue import Task


# Валидный UUID в payload — _parse_plan_id возвращает строку
def test_parse_plan_id_valid_uuid() -> None:
    payload = {"plan_id": "11111111-2222-3333-4444-555555555555"}
    assert _parse_plan_id(payload) == "11111111-2222-3333-4444-555555555555"


# payload без plan_id — ValueError → mark_failed в worker'е
def test_parse_plan_id_missing_raises() -> None:
    with pytest.raises(ValueError, match="без plan_id"):
        _parse_plan_id({})


# Невалидный формат plan_id — ValueError
def test_parse_plan_id_invalid_format_raises() -> None:
    with pytest.raises(ValueError, match="invalid plan_id"):
        _parse_plan_id({"plan_id": "not-a-uuid"})


# None в plan_id трактуется как отсутствующий ключ
def test_parse_plan_id_none_value_raises() -> None:
    with pytest.raises(ValueError, match="без plan_id"):
        _parse_plan_id({"plan_id": None})


# Permanent набор включает ValueError/NotImplementedError/KeyError
def test_permanent_exceptions_includes_value_and_notimpl() -> None:
    assert ValueError in _PERMANENT_EXCEPTIONS
    assert NotImplementedError in _PERMANENT_EXCEPTIONS
    assert KeyError in _PERMANENT_EXCEPTIONS


# Transient набор включает BrowserUnavailableError/Timeout/Connection/grpc.RpcError
def test_temporary_exceptions_includes_browser_and_timeout() -> None:
    assert BrowserUnavailableError in _TEMPORARY_EXCEPTIONS
    assert asyncio.TimeoutError in _TEMPORARY_EXCEPTIONS
    assert ConnectionError in _TEMPORARY_EXCEPTIONS
    assert grpc.RpcError in _TEMPORARY_EXCEPTIONS


# Permanent и Temporary не пересекаются
def test_exception_classes_dont_overlap() -> None:
    perm = set(_PERMANENT_EXCEPTIONS)
    temp = set(_TEMPORARY_EXCEPTIONS)
    assert perm.isdisjoint(temp), f"Перекрытие классов ошибок: {perm & temp}"


# Сборщик событий: started + finished*N + complete(ok=True) → ok=True, steps_executed=N
@pytest.mark.asyncio
async def test_execute_plan_stream_success() -> None:
    events = [
        _started_event("step_a", 0),
        _finished_event("step_a", 0),
        _started_event("step_b", 1),
        _finished_event("step_b", 1),
        _complete_event(ok=True, total_steps=2, duration_ms=1234),
    ]
    client = _FakeRunPlanClient(events)
    result = await _execute_plan_stream(client, plan_json="{}", variables_json="{}", task_id=99)
    assert result["ok"] is True
    assert result["steps_executed"] == 2
    assert result["total_steps"] == 2
    assert result["duration_ms"] == 1234
    assert result["failed_step"] is None
    assert result["error"] is None
    assert result["checkpoints"] == []


# StepFailed в стриме → ok=False, failed_step/error заполнены
@pytest.mark.asyncio
async def test_execute_plan_stream_step_failed() -> None:
    events = [
        _started_event("step_a", 0),
        _failed_event("step_a", 0, "selector not found"),
        _complete_event(ok=False, total_steps=1, error="aborted"),
    ]
    client = _FakeRunPlanClient(events)
    result = await _execute_plan_stream(client, plan_json="{}", variables_json="{}", task_id=1)
    assert result["ok"] is False
    assert result["failed_step"] == "step_a"
    # PlanComplete.error перезаписывает промежуточный error (финальный диагноз важнее)
    assert result["error"] == "aborted"
    assert result["steps_executed"] == 0


# Checkpoint собирается в result.checkpoints, но не делает ok=False сам по себе
@pytest.mark.asyncio
async def test_execute_plan_stream_checkpoint_collected() -> None:
    events = [
        _checkpoint_event("https://facebook.com/checkpoint/abc", "captcha"),
        _started_event("step_a", 0),
        _finished_event("step_a", 0),
        _complete_event(ok=True, total_steps=1),
    ]
    client = _FakeRunPlanClient(events)
    result = await _execute_plan_stream(client, plan_json="{}", variables_json="{}", task_id=2)
    assert result["ok"] is True
    assert len(result["checkpoints"]) == 1
    assert result["checkpoints"][0]["detail"] == "captcha"


# process_one_task с невалидным payload вызывает mark_failed и НЕ обращается к клиенту
@pytest.mark.asyncio
async def test_process_one_task_invalid_payload_marks_failed(monkeypatch) -> None:
    task = _fake_task(payload={})  # без plan_id
    mark_failed_mock = AsyncMock()
    monkeypatch.setattr(worker_main, "mark_failed", mark_failed_mock)
    monkeypatch.setattr(worker_main, "load_plan", AsyncMock())  # не должна вызываться

    client = AsyncMock()
    await process_one_task(engine=None, task=task, client=client)

    mark_failed_mock.assert_awaited_once()
    err_msg = mark_failed_mock.call_args.kwargs["error"]
    assert "plan_id" in err_msg
    client.run_plan.assert_not_called()


# process_one_task если plan не найден / архивирован → mark_failed
@pytest.mark.asyncio
async def test_process_one_task_plan_not_found_marks_failed(monkeypatch) -> None:
    task = _fake_task(payload={"plan_id": "11111111-2222-3333-4444-555555555555"})
    monkeypatch.setattr(worker_main, "load_plan", AsyncMock(return_value=None))
    mark_failed_mock = AsyncMock()
    monkeypatch.setattr(worker_main, "mark_failed", mark_failed_mock)

    client = AsyncMock()
    await process_one_task(engine=None, task=task, client=client)

    mark_failed_mock.assert_awaited_once()
    err_msg = mark_failed_mock.call_args.kwargs["error"]
    assert "plan not found" in err_msg or "archived" in err_msg
    client.run_plan.assert_not_called()


# BrowserUnavailableError из stream → requeue_for_retry (transient)
@pytest.mark.asyncio
async def test_process_one_task_browser_unavailable_requeues(monkeypatch) -> None:
    task = _fake_task(payload={"plan_id": "11111111-2222-3333-4444-555555555555"})
    monkeypatch.setattr(
        worker_main,
        "load_plan",
        AsyncMock(
            return_value={
                "id": "11111111-2222-3333-4444-555555555555",
                "name": "test",
                "schema_version": 1,
                "steps": [{"step": "noop"}],
                "variables": {},
            }
        ),
    )

    async def _boom(*_a, **_kw):
        # Симулируем BrowserUnavailableError из circuit-breaker
        from core.browser.circuit_breaker import CircuitOpenError

        raise BrowserUnavailableError(CircuitOpenError("test"))

    monkeypatch.setattr(worker_main, "_execute_plan_stream", _boom)
    requeue_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(worker_main, "requeue_for_retry", requeue_mock)

    client = AsyncMock()
    await process_one_task(engine=None, task=task, client=client)

    requeue_mock.assert_awaited_once()


# ====================== helpers ======================


def _fake_task(payload: dict[str, Any]) -> Task:
    return Task(
        id=42,
        task_type="plan_run",
        status="running",
        idempotency_key="test-key",
        payload=payload,
        attempt_count=0,
        max_attempts=5,
        requested_by="test",
    )


class _FakeRunPlanClient:
    """Минимальный fake клиент: run_plan(...) возвращает async-итератор по событиям."""

    def __init__(self, events: list[Any]) -> None:
        self._events = events

    def run_plan(self, plan_json: str, variables_json: str):
        return _aiter(self._events)


async def _aiter(items):
    for item in items:
        yield item


def _event_with(field_name: str, value: Any) -> Any:
    """PlanEvent-подобный объект с одним заполненным oneof-полем."""

    class _Evt:
        def __init__(self) -> None:
            self._field = field_name
            setattr(self, field_name, value)

        def HasField(self, name: str) -> bool:
            return name == self._field

    return _Evt()


def _started_event(step: str, index: int) -> Any:
    return _event_with("started", SimpleNamespace(step=step, index=index, timestamp_ms=0))


def _finished_event(step: str, index: int, detail: str = "{}") -> Any:
    return _event_with(
        "finished",
        SimpleNamespace(step=step, index=index, timestamp_ms=0, detail_json=detail),
    )


def _failed_event(step: str, index: int, error: str) -> Any:
    return _event_with(
        "failed",
        SimpleNamespace(step=step, index=index, error=error, timestamp_ms=0),
    )


def _complete_event(
    *, ok: bool, total_steps: int = 0, duration_ms: int = 0, error: str = ""
) -> Any:
    return _event_with(
        "complete",
        SimpleNamespace(ok=ok, total_steps=total_steps, duration_ms=duration_ms, error=error),
    )


def _checkpoint_event(url: str, detail: str) -> Any:
    return _event_with("checkpoint", SimpleNamespace(url=url, detail=detail))

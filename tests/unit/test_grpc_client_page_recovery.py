# -*- coding: utf-8 -*-
"""Fail-closed scanner lifecycle and bounded process-session recovery.

Scan-page loss never escalates into a browser reconnect or Vision restart.
Lifecycle-changing recovery is available only through the exclusive,
capability-authorized maintenance RPC. A lost process-local browser-agent
session may be rebound once because that does not change the external profile.
"""

from __future__ import annotations

import asyncio
import types
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import grpc
import pytest

from clients.python_grpc.client import (
    BrowserAgentClient,
    BrowserAgentConfig,
    ScanResult,
    _is_missing_browser_session_error,
)
from clients.python_grpc.v1 import scanner_pb2
from core.deadlines import bind_absolute_deadline
from core.scanner.models import SCANNER_METRICS_CONTRACT_REVISION

_PAGE_ERR = "Основная страница браузера недоступна"
_SESSION_ERR = "Сессия 1ab03f5f-ad9d-4931-82b0-cd10661ead0b не найдена"


def test_old_scan_complete_wire_message_defaults_contract_revision_to_zero() -> None:
    old_wire_payload = scanner_pb2.ScanComplete().SerializeToString()

    decoded = scanner_pb2.ScanComplete.FromString(old_wire_payload)

    assert decoded.metrics_contract_revision == 0


def _error_event(message: str):
    """Фейковый ScanCycleEvent с заполненным oneof error."""
    err = types.SimpleNamespace(message=message, attempt=1, recoverable=True)
    return types.SimpleNamespace(error=err, HasField=lambda f: f == "error")


def _complete_event(
    metrics_contract_revision: int = SCANNER_METRICS_CONTRACT_REVISION,
):
    """Фейковый ScanCycleEvent complete с пустым результатом."""
    phase = types.SimpleNamespace(refresh_ms=0, first_row_ms=0, scroll_ms=0, parse_ms=0, total_ms=0)
    comp = types.SimpleNamespace(
        all_rows=[],
        total_passes=1,
        duration_seconds=0.0,
        dismissed_modals=[],
        unknown_modal_artifacts=[],
        phase_timings=phase,
        partial_row_ids=[],
        warnings=[],
        empty_reason="",
        rows_with_all_metrics_empty=0,
        metrics_contract_revision=metrics_contract_revision,
    )
    return types.SimpleNamespace(complete=comp, HasField=lambda f: f == "complete")


class _FakeStream:
    """Async-итератор поверх готового списка событий + cancel() как у gRPC-стрима."""

    def __init__(self, events):
        self._events = list(events)
        self.cancelled = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)

    def cancel(self):
        self.cancelled = True


def _make_client(stream_batches):
    """Клиент с замоканным scanner-stub: каждый вызов RunScanCycle отдаёт следующий батч."""
    client = BrowserAgentClient(BrowserAgentConfig())
    client._session_id = "sess-1"  # ensure_browser_session() → no-op
    calls = {"run": 0, "timeouts": [], "requests": []}

    def _run(req, *, timeout):
        idx = calls["run"]
        calls["run"] += 1
        calls["timeouts"].append(timeout)
        calls["requests"].append(req)
        return _FakeStream(stream_batches[idx])

    client._scanner_stub = types.SimpleNamespace(RunScanCycle=_run)
    client.reconnect_browser = AsyncMock()
    return client, calls


@pytest.mark.asyncio
async def test_scan_page_loss_never_reconnects_or_retries():
    client, calls = _make_client([[_error_event(_PAGE_ERR)]])

    with pytest.raises(RuntimeError, match="страница браузера недоступна"):
        async for _ in client.run_scan_cycle(ad_account_id="123"):
            pass

    assert client.reconnect_browser.await_count == 0
    assert calls["run"] == 1


@pytest.mark.asyncio
async def test_scan_generic_error_not_page_recovered():
    client, calls = _make_client([[_error_event("am_tabular: непредвиденная ошибка")]])

    with pytest.raises(RuntimeError):
        async for _ in client.run_scan_cycle(ad_account_id="123"):
            pass

    assert client.reconnect_browser.await_count == 0
    assert calls["run"] == 1


# ─── Session-recovery: «Сессия не найдена» как stream error-event (баг рестарта browser_agent) ───


# Регресс: session-not-found приходит как stream error-event → RuntimeError БЕЗ gRPC-кода.
# Раньше детектор требовал code()==NOT_FOUND и этот путь не ловил → observer залипал на
# протухшей сессии после рестарта browser_agent (мониторинг стоял до ручного рестарта observer).
def test_missing_session_detected_from_stream_event_message():
    assert _is_missing_browser_session_error(RuntimeError(_SESSION_ERR)) is True
    assert _is_missing_browser_session_error(RuntimeError("Session xyz not found")) is True


# gRPC-статус NOT_FOUND (unary) тоже распознаётся как потеря сессии (фолбэк по коду).
def test_missing_session_detected_from_grpc_not_found_code():
    exc = types.SimpleNamespace(
        code=lambda: grpc.StatusCode.NOT_FOUND, details=lambda: "session lost"
    )
    assert _is_missing_browser_session_error(exc) is True


# Обычная ошибка (не сессия, не NOT_FOUND) — НЕ распознаётся как потеря сессии.
def test_generic_error_not_missing_session():
    assert _is_missing_browser_session_error(RuntimeError("am_tabular: timeout")) is False
    other = types.SimpleNamespace(code=lambda: grpc.StatusCode.UNAVAILABLE, details=lambda: "")
    assert _is_missing_browser_session_error(other) is False


# E2E: session-lost через stream error-event → recovery (новая сессия) → повтор отдаёт ScanResult.
@pytest.mark.asyncio
async def test_scan_recovers_on_missing_session_stream_event():
    client, calls = _make_client([[_error_event(_SESSION_ERR)], [_complete_event()]])
    client._recover_missing_browser_session = AsyncMock(return_value="sess-2")

    results = [ev async for ev in client.run_scan_cycle(ad_account_id="123")]

    assert client._recover_missing_browser_session.await_count == 1
    assert calls["run"] == 2
    result = next(r for r in results if isinstance(r, ScanResult))
    assert result.metrics_contract_revision == SCANNER_METRICS_CONTRACT_REVISION


@pytest.mark.asyncio
async def test_scan_rpc_receives_finite_absolute_deadline() -> None:
    client, calls = _make_client([[_complete_event()]])

    with bind_absolute_deadline(datetime.now(UTC) + timedelta(seconds=0.5)):
        results = [
            event
            async for event in client.run_scan_cycle(
                ad_account_id="123",
            )
        ]

    assert any(isinstance(result, ScanResult) for result in results)
    assert len(calls["timeouts"]) == 1
    assert 0 < calls["timeouts"][0] <= 0.5


@pytest.mark.asyncio
async def test_scan_request_delivers_presentation_columns_without_touching_metrics() -> None:
    client, calls = _make_client([[_complete_event()]])
    am_columns_qs = "columns=name%2Cspend&column_preset=999"

    results = [
        event
        async for event in client.run_scan_cycle(
            ad_account_id="123",
            am_columns_qs=am_columns_qs,
        )
    ]

    assert any(isinstance(result, ScanResult) for result in results)
    assert calls["requests"][0].am_columns_qs == am_columns_qs


@pytest.mark.asyncio
async def test_scan_cancellation_cancels_grpc_stream() -> None:
    started = asyncio.Event()

    class _BlockingStream:
        def __init__(self) -> None:
            self.cancelled = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            started.set()
            await asyncio.Future()

        def cancel(self) -> None:
            self.cancelled = True

    stream = _BlockingStream()
    client = BrowserAgentClient(BrowserAgentConfig())
    client._session_id = "sess-1"
    client._scanner_stub = types.SimpleNamespace(RunScanCycle=lambda _req, *, timeout: stream)

    async def consume() -> None:
        async for _ in client.run_scan_cycle(ad_account_id="123", timeout_seconds=30):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert stream.cancelled is True

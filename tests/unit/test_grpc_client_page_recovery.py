# -*- coding: utf-8 -*-
"""Self-heal Layer 2: run_scan_cycle при ошибке «страница недоступна» эскалирует reconnect.

Контекст бага: browser-agent шлёт ScanError(event.error) с текстом
'Основная страница браузера недоступна', клиент превращал его в RuntimeError и пробрасывал
наверх БЕЗ восстановления (это не gRPC NOT_FOUND session-lost) — observer падал каждые ~90с
и мониторинг лежал ~104 минуты. Теперь клиент один раз дёргает reconnect_browser и повторяет
скан (gated флагом auto_recover_page из vision_config).
"""

from __future__ import annotations

import types
from unittest.mock import AsyncMock

import grpc
import pytest

from clients.python_grpc.client import (
    BrowserAgentClient,
    BrowserAgentConfig,
    ScanResult,
    _is_missing_browser_session_error,
)

_PAGE_ERR = "Основная страница браузера недоступна"
_SESSION_ERR = "Сессия 1ab03f5f-ad9d-4931-82b0-cd10661ead0b не найдена"


def _error_event(message: str):
    """Фейковый ScanCycleEvent с заполненным oneof error."""
    err = types.SimpleNamespace(message=message, attempt=1, recoverable=True)
    return types.SimpleNamespace(error=err, HasField=lambda f: f == "error")


def _complete_event():
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
    calls = {"run": 0}

    def _run(_req):
        idx = calls["run"]
        calls["run"] += 1
        return _FakeStream(stream_batches[idx])

    client._scanner_stub = types.SimpleNamespace(RunScanCycle=_run)
    client.reconnect_browser = AsyncMock()
    return client, calls


# Сценарий: первая попытка — page-unavailable, после reconnect повтор отдаёт ScanResult.
@pytest.mark.asyncio
async def test_scan_recovers_on_missing_primary_page():
    client, calls = _make_client([[_error_event(_PAGE_ERR)], [_complete_event()]])

    results = [ev async for ev in client.run_scan_cycle(auto_recover_page=True)]

    assert client.reconnect_browser.await_count == 1
    assert calls["run"] == 2
    assert any(isinstance(r, ScanResult) for r in results)


# Сценарий: флаг выключен — НЕ восстанавливаемся, ошибка пробрасывается, reconnect не зовём.
@pytest.mark.asyncio
async def test_scan_no_recovery_when_flag_disabled():
    client, calls = _make_client([[_error_event(_PAGE_ERR)]])

    with pytest.raises(RuntimeError, match="страница браузера недоступна"):
        async for _ in client.run_scan_cycle(auto_recover_page=False):
            pass

    assert client.reconnect_browser.await_count == 0
    assert calls["run"] == 1


# Сценарий: восстановление ровно один раз — если и после reconnect та же ошибка, пробрасываем.
@pytest.mark.asyncio
async def test_scan_recovery_attempted_only_once():
    client, calls = _make_client([[_error_event(_PAGE_ERR)], [_error_event(_PAGE_ERR)]])

    with pytest.raises(RuntimeError, match="страница браузера недоступна"):
        async for _ in client.run_scan_cycle(auto_recover_page=True):
            pass

    assert client.reconnect_browser.await_count == 1
    assert calls["run"] == 2


# Сценарий: обычная (не page) ошибка скана НЕ триггерит page-recovery — reconnect не зовём.
@pytest.mark.asyncio
async def test_scan_generic_error_not_page_recovered():
    client, calls = _make_client([[_error_event("am_tabular: непредвиденная ошибка")]])

    with pytest.raises(RuntimeError):
        async for _ in client.run_scan_cycle(auto_recover_page=True):
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
# Зеркало page-recovery, но для протухшей сессии. Session-recovery НЕ gated флагом (старый путь).
@pytest.mark.asyncio
async def test_scan_recovers_on_missing_session_stream_event():
    client, calls = _make_client([[_error_event(_SESSION_ERR)], [_complete_event()]])
    client._recover_missing_browser_session = AsyncMock(return_value="sess-2")

    results = [ev async for ev in client.run_scan_cycle(auto_recover_page=False)]

    assert client._recover_missing_browser_session.await_count == 1
    assert calls["run"] == 2
    assert any(isinstance(r, ScanResult) for r in results)

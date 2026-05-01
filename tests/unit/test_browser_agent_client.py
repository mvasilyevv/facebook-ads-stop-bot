# -*- coding: utf-8 -*-
"""Тесты gRPC-клиента browser-agent."""

from __future__ import annotations

from types import SimpleNamespace

import grpc
import pytest

from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig, ScanProgress
from clients.python_grpc.v1 import browser_session_pb2, scanner_pb2


class _FakeRpcError(grpc.RpcError):
    """Имитирует gRPC-ошибку с code/details."""

    def __init__(self, code, details: str):
        self._code = code
        self._details = details

    def code(self):
        return self._code

    def details(self):
        return self._details

    def __str__(self):
        return self._details


class _FakeScanStream:
    """Имитирует потоковый RPC с поддержкой отмены."""

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


class _FailingScanStream:
    """Имитирует потоковый RPC, который падает до первого события."""

    def __init__(self, error):
        self._error = error
        self.cancelled = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise self._error

    def cancel(self):
        self.cancelled = True


def _start_browser_response(session_id: str):
    return browser_session_pb2.StartBrowserResponse(
        session_id=session_id,
        profile=browser_session_pb2.VisionProfile(
            folder_id="folder-1",
            profile_id="profile-1",
            cdp_port=9222,
        ),
    )


# Проверяем, что ScanError из browser-agent не проглатывается и поднимается как RuntimeError.
@pytest.mark.asyncio
async def test_run_scan_cycle_raises_on_error_event():
    async def error_stream(_request):
        yield scanner_pb2.ScanCycleEvent(
            error=scanner_pb2.ScanError(
                message="Схема колонок Ads Manager изменилась",
                recoverable=False,
                attempt=1,
            )
        )

    client = BrowserAgentClient(BrowserAgentConfig())
    client._scanner_stub = SimpleNamespace(RunScanCycle=error_stream)
    client._session_id = "session-1"

    with pytest.raises(RuntimeError, match="Схема колонок Ads Manager изменилась"):
        async for _event in client.run_scan_cycle():
            pass


# Проверяем, что промежуточное событие отдаёт новые строки и отменяет поток при досрочном выходе.
@pytest.mark.asyncio
async def test_run_scan_cycle_yields_progress_rows_and_cancels_on_break():
    row = scanner_pb2.ScannedAdRow(
        fb_ad_id="120246605325150334",
        campaign_name="Campaign",
        adset_name="Adset",
        ad_name="Ad",
        delivery_status="ACTIVE",
        spend="12.34",
    )
    stream = _FakeScanStream(
        [
            scanner_pb2.ScanCycleEvent(
                progress=scanner_pb2.ScanProgress(
                    pass_number=1,
                    rows_so_far=1,
                    new_rows=[row],
                    scroll_metrics=scanner_pb2.ScrollMetrics(at_bottom=False),
                )
            )
        ]
    )

    client = BrowserAgentClient(BrowserAgentConfig())
    client._scanner_stub = SimpleNamespace(RunScanCycle=lambda _request: stream)
    client._session_id = "session-1"

    scan_events = client.run_scan_cycle()
    event = await anext(scan_events)
    assert isinstance(event, ScanProgress)
    assert event.new_rows_count == 1
    assert event.new_rows[0].fb_ad_id == "120246605325150334"
    await scan_events.aclose()

    assert stream.cancelled is True


# Проверяем, что navigate идет через BrowserSessionService, а не через ScannerService.
@pytest.mark.asyncio
async def test_navigate_uses_browser_session_stub():
    called_requests = []

    async def browser_navigate(request, **_kwargs):
        called_requests.append(request)

    async def scanner_navigate(_request):
        raise AssertionError("navigate не должен вызывать ScannerService")

    client = BrowserAgentClient(BrowserAgentConfig())
    client._browser_stub = SimpleNamespace(Navigate=browser_navigate)
    client._scanner_stub = SimpleNamespace(Navigate=scanner_navigate)
    client._session_id = "session-1"

    await client.navigate("https://example.com/", wait_until="load")

    assert len(called_requests) == 1
    assert called_requests[0].session_id == "session-1"
    assert called_requests[0].url == "https://example.com/"
    assert called_requests[0].wait_until == "load"


# Проверяем, что reconnect создаёт новую сессию, если browser-agent потерял старый session_id.
@pytest.mark.asyncio
async def test_reconnect_browser_starts_new_session_when_session_is_missing():
    reconnect_requests = []
    start_requests = []

    async def reconnect_browser(request, **_kwargs):
        reconnect_requests.append(request)
        raise _FakeRpcError(grpc.StatusCode.NOT_FOUND, "Сессия session-1 не найдена")

    async def start_browser(request, **_kwargs):
        start_requests.append(request)
        return _start_browser_response("session-2")

    client = BrowserAgentClient(
        BrowserAgentConfig(
            vision_x_token="token",
            vision_api_url="http://vision.local",
            vision_profile_id="profile-1",
            vision_folder_id="folder-1",
        )
    )
    client._browser_stub = SimpleNamespace(
        ReconnectBrowser=reconnect_browser,
        StartBrowser=start_browser,
    )
    client._session_id = "session-1"

    session_id = await client.reconnect_browser()

    assert session_id == "session-2"
    assert client.session_id == "session-2"
    assert reconnect_requests[0].session_id == "session-1"
    assert start_requests[0].vision_profile_id == "profile-1"
    assert start_requests[0].vision_folder_id == "folder-1"


# Проверяем, что обычный scanner RPC один раз пересоздаёт сессию и повторяется.
@pytest.mark.asyncio
async def test_find_toggle_cell_recovers_missing_session_and_retries_once():
    start_requests = []
    find_requests = []

    async def start_browser(request, **_kwargs):
        start_requests.append(request)
        return _start_browser_response("session-2")

    async def find_toggle_cell(request, **_kwargs):
        find_requests.append(request)
        if len(find_requests) == 1:
            raise _FakeRpcError(grpc.StatusCode.NOT_FOUND, "Сессия session-1 не найдена")
        return scanner_pb2.FindToggleCellResponse(
            found=True,
            cell_x=100,
            cell_y=200,
            aria_checked="false",
        )

    client = BrowserAgentClient(BrowserAgentConfig(vision_profile_id="profile-1"))
    client._browser_stub = SimpleNamespace(StartBrowser=start_browser)
    client._scanner_stub = SimpleNamespace(FindToggleCell=find_toggle_cell)
    client._session_id = "session-1"

    result = await client.find_toggle_cell("120246283878900334")

    assert result == {
        "found": True,
        "cell_x": 100,
        "cell_y": 200,
        "aria_checked": "false",
    }
    assert [request.session_id for request in find_requests] == ["session-1", "session-2"]
    assert start_requests[0].vision_profile_id == "profile-1"


# Проверяем, что scan stream восстанавливается, если сессия потерялась до первого события.
@pytest.mark.asyncio
async def test_run_scan_cycle_recovers_missing_session_before_first_event():
    start_requests = []
    scan_requests = []
    stale_error = _FakeRpcError(grpc.StatusCode.NOT_FOUND, "Сессия session-1 не найдена")
    first_stream = _FailingScanStream(stale_error)
    second_stream = _FakeScanStream(
        [
            scanner_pb2.ScanCycleEvent(
                progress=scanner_pb2.ScanProgress(
                    pass_number=1,
                    rows_so_far=0,
                    scroll_metrics=scanner_pb2.ScrollMetrics(at_bottom=False),
                )
            )
        ]
    )

    async def start_browser(request, **_kwargs):
        start_requests.append(request)
        return _start_browser_response("session-2")

    def run_scan_cycle(request):
        scan_requests.append(request)
        return first_stream if len(scan_requests) == 1 else second_stream

    client = BrowserAgentClient(BrowserAgentConfig(vision_profile_id="profile-1"))
    client._browser_stub = SimpleNamespace(StartBrowser=start_browser)
    client._scanner_stub = SimpleNamespace(RunScanCycle=run_scan_cycle)
    client._session_id = "session-1"

    scan_events = client.run_scan_cycle()
    event = await anext(scan_events)
    await scan_events.aclose()

    assert isinstance(event, ScanProgress)
    assert event.pass_number == 1
    assert [request.session_id for request in scan_requests] == ["session-1", "session-2"]
    assert first_stream.cancelled is True
    assert second_stream.cancelled is True
    assert start_requests[0].vision_profile_id == "profile-1"

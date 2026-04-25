# -*- coding: utf-8 -*-
"""Тесты gRPC-клиента browser-agent."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig, ScanProgress
from clients.python_grpc.v1 import scanner_pb2


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

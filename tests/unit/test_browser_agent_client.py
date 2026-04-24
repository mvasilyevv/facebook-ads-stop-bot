# -*- coding: utf-8 -*-
"""Тесты gRPC-клиента browser-agent."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig
from clients.python_grpc.v1 import scanner_pb2


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

# -*- coding: utf-8 -*-
"""GetWorkerHealth различает живой процесс observer и включённое сканирование."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import core.ai_assistant.tools.ops.get_worker_health as worker_health
from core.ai_assistant.tools.base import ToolContext


@pytest.mark.asyncio
async def test_reports_paused_observer_even_when_all_heartbeats_exist(monkeypatch) -> None:
    redis = MagicMock()
    redis.get = AsyncMock(return_value=json.dumps({"status": "ONLINE", "ts": 123}))
    monkeypatch.setattr(
        worker_health,
        "read_observer_runtime",
        AsyncMock(
            return_value={
                "status": "paused",
                "last_successful_scan_at": "2026-07-16T08:42:52Z",
                "next_scan_at": "2026-07-16T09:48:17Z",
            }
        ),
    )
    monkeypatch.setattr(worker_health, "load_scanning_enabled", AsyncMock(return_value=False))

    result = await worker_health.GetWorkerHealthTool().run(
        ToolContext(client_key="test", engine=MagicMock(), redis_client=redis),
        {},
    )

    assert "runtime=paused" in result
    assert "scanning_enabled=False" in result
    assert "нельзя описывать как «всё работает штатно»" in result

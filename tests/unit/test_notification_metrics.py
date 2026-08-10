from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pytest
from prometheus_client import REGISTRY

from core.telegram.notifications import refresh_notification_metrics


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _Connection:
    def __init__(self, results: list[list[Any]]) -> None:
        self.results = list(results)
        self.statements: list[str] = []

    async def execute(self, statement: Any) -> _Result:
        self.statements.append(str(statement))
        return _Result(self.results.pop(0))


class _Context:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _Connection:
        return self.connection

    async def __aexit__(self, *_args: Any) -> None:
        return None


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def connect(self) -> _Context:
        return _Context(self.connection)


@pytest.mark.asyncio
async def test_notification_metrics_are_refreshed_from_durable_rows() -> None:
    connection = _Connection(
        [
            [SimpleNamespace(severity="critical", age_seconds=12.5)],
            [SimpleNamespace(state="dead", total=4, recent=1, p50=1.0, p95=8.0, p99=9.0)],
        ]
    )

    await refresh_notification_metrics(_Engine(connection))  # type: ignore[arg-type]

    assert (
        REGISTRY.get_sample_value(
            "fb_agent_notification_oldest_pending_age_seconds",
            {"severity": "critical"},
        )
        == 12.5
    )
    assert (
        REGISTRY.get_sample_value("fb_agent_notification_delivery_terminal_rows", {"state": "dead"})
        == 4
    )
    assert (
        REGISTRY.get_sample_value(
            "fb_agent_notification_delivery_terminal_events_5m", {"state": "dead"}
        )
        == 1
    )
    assert (
        REGISTRY.get_sample_value(
            "fb_agent_notification_delivery_latency_quantile_seconds",
            {"state": "dead", "quantile": "0.95"},
        )
        == 8
    )
    assert "scheduled_at <= NOW()" in connection.statements[0]
    assert "completed_at - e.created_at" in connection.statements[1]
    assert "INTERVAL '7 days'" in connection.statements[1]


@pytest.mark.asyncio
async def test_missing_latency_samples_export_nan_not_false_zero() -> None:
    connection = _Connection([[], []])

    await refresh_notification_metrics(_Engine(connection))  # type: ignore[arg-type]

    value = REGISTRY.get_sample_value(
        "fb_agent_notification_delivery_latency_quantile_seconds",
        {"state": "sent", "quantile": "0.95"},
    )
    assert value is not None and math.isnan(value)

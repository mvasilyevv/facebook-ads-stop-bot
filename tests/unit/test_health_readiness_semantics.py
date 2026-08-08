from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import Response

from apps.api.routers import health


@pytest.mark.asyncio
async def test_redis_outage_is_explicit_degraded_but_not_api_readiness_failure(
    monkeypatch,
) -> None:
    monkeypatch.setattr(health, "_check_postgres", AsyncMock(return_value=True))
    monkeypatch.setattr(health, "_check_redis", AsyncMock(return_value=False))
    health.reset_readyz_cache()
    response = Response()

    result = await health.readyz(response, engine=object(), redis=object())  # type: ignore[arg-type]

    assert response.status_code == 200
    assert result == {
        "ready": True,
        "postgres": True,
        "redis": False,
        "degraded": ["redis_unavailable"],
        "cached": False,
    }


@pytest.mark.asyncio
async def test_system_readiness_uses_only_postgres_control_plane(
    monkeypatch,
) -> None:
    monkeypatch.setattr(health, "_check_postgres", AsyncMock(return_value=True))
    now = datetime.now(UTC)
    monkeypatch.setattr(
        health,
        "fetch_operator_scan_state",
        AsyncMock(
            return_value={
                "enabled": True,
                "last_scan_at": now,
                "actors": [
                    {
                        "ad_account_id": "123",
                        "last_progress_at": now,
                        "last_snapshot_at": now,
                        "error": None,
                    }
                ],
            }
        ),
    )
    monkeypatch.setattr(health, "resolve_scan_account_ids", AsyncMock(return_value=["123"]))
    monkeypatch.setattr(health, "_load_money_task_failures", AsyncMock(return_value=(0, 0)))
    response = Response()

    result = await health.system_readyz(
        response,
        engine=object(),  # type: ignore[arg-type]
    )

    assert response.status_code == 200
    assert result.ready is True
    assert result.infrastructure_ready is True
    assert result.overall == "HEALTHY"
    assert result.actors_active == 1
    assert result.blockers == []
    assert result.degraded == []

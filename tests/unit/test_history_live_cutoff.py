# -*- coding: utf-8 -*-
"""Тесты live-среза истории: граница суток кабинета как на dashboard."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.api.routers import history as history_router


# Сценарий: в live попадают только объявления, наблюдавшиеся после cutoff performance.
@pytest.mark.asyncio
async def test_load_live_ads_filters_by_performance_cutoff(monkeypatch):
    cutoff = datetime(2026, 5, 18, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(
        history_router,
        "_resolve_dashboard_performance_cutoff",
        AsyncMock(return_value=cutoff),
    )

    fresh = MagicMock()
    fresh.fb_ad_id = "fresh"
    fresh.spend = Decimal("8.51")
    fresh.clicks = 1
    fresh.leads = 0
    fresh.registrations = 0
    fresh.deposits = 0
    fresh.last_observed_at = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)

    stale = MagicMock()
    stale.fb_ad_id = "stale"
    stale.spend = Decimal("76.66")
    stale.clicks = 0
    stale.leads = 0
    stale.registrations = 0
    stale.deposits = 0
    stale.last_observed_at = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [
        (fresh, "Ad Fresh", "Camp", "OFFER"),
    ]
    mock_db.execute = AsyncMock(return_value=mock_result)

    captured: dict = {}

    async def fake_execute(query):
        captured["query"] = query
        return mock_result

    mock_db.execute = fake_execute

    grouped = await history_router._load_live_ads_for_today(mock_db)

    assert list(grouped.keys()) == ["fresh"]
    assert grouped["fresh"]["spend"] == Decimal("8.51")
    assert captured.get("query") is not None


# Сценарий: summary и timeline не подмешивают live, если за сегодня уже есть архив.
def test_should_merge_live_false_when_archive_for_today_exists():
    arch = MagicMock()
    arch.started_at = datetime.combine(date.today(), datetime.min.time())

    assert (
        history_router._should_merge_live_for_today(
            date.today(),
            date.today(),
            [arch],
        )
        is False
    )


# Сценарий: без архива за сегодня live подмешивается в период, включающий сегодня.
def test_should_merge_live_true_without_today_archive():
    assert (
        history_router._should_merge_live_for_today(
            date.today().replace(day=1),
            date.today(),
            [],
        )
        is True
    )

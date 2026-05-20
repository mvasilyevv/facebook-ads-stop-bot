# -*- coding: utf-8 -*-
"""Тесты эндпоинта /history/timeline."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.api.routers import history as history_router


# Сценарий: период включает сегодня, архивов за сегодня нет — добавляется live-точка.
@pytest.mark.asyncio
async def test_history_timeline_appends_live_point_for_today(monkeypatch):
    today = date.today()
    archive = MagicMock()
    archive.started_at = MagicMock()
    archive.started_at.date.return_value = today.replace(day=max(1, today.day - 1))
    archive.campaigns_json = None
    archive.summary_json = {
        "spend": "10.00",
        "clicks": 1,
        "leads": 0,
        "registrations": 0,
        "deposits": 0,
    }

    monkeypatch.setattr(history_router, "_load_archives", AsyncMock(return_value=[archive]))
    monkeypatch.setattr(
        history_router,
        "_load_fake_deposits_by_campaign",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        history_router,
        "_load_fake_deposits_map",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        history_router,
        "_load_live_ads_for_today",
        AsyncMock(
            return_value={
                "ad-1": {
                    "fb_ad_id": "ad-1",
                    "spend": Decimal("5.00"),
                    "clicks": 2,
                    "leads": 1,
                    "regs": 0,
                    "deps": 0,
                },
            }
        ),
    )

    mock_db = AsyncMock()
    result = await history_router.get_history_timeline(
        date_from=(today.replace(day=1)).isoformat(),
        date_to=today.isoformat(),
        offer_code=None,
        offer_codes=None,
        campaign_name=None,
        db=mock_db,
    )

    dates = [p.date for p in result]
    assert today.isoformat() in dates
    today_point = next(p for p in result if p.date == today.isoformat())
    assert today_point.spend == Decimal("5.00")
    assert today_point.clicks == 2
    assert today_point.leads == 1

# -*- coding: utf-8 -*-
"""Тесты summary/history: live-загрузка и offer_codes."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from apps.api.routers import history as history_router


# Сценарий: summary передаёт offer_code в _load_live_ads_for_today, а не set кампаний.
@pytest.mark.asyncio
async def test_history_summary_calls_live_loader_with_offer_code(monkeypatch):
    today = date.today()
    captured: dict = {}

    async def fake_live(db, offer_code=None, campaign_name=None, offer_codes=None):
        captured["offer_code"] = offer_code
        captured["offer_codes"] = offer_codes
        return {}

    monkeypatch.setattr(
        history_router,
        "_load_campaigns_for_offers",
        AsyncMock(return_value={"camp-1"}),
    )
    monkeypatch.setattr(history_router, "_load_archives", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        history_router,
        "_aggregate_archives",
        lambda *a, **k: {
            "spend": Decimal("0"),
            "clicks": 0,
            "leads": 0,
            "regs": 0,
            "deps": 0,
        },
    )
    monkeypatch.setattr(history_router, "_load_live_ads_for_today", fake_live)
    monkeypatch.setattr(
        history_router,
        "_count_alerts_in_range",
        AsyncMock(return_value=(0, 0)),
    )
    monkeypatch.setattr(
        history_router,
        "_count_disables_in_range",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        history_router,
        "_fake_deposit_adjustment_for_offer_codes",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(history_router, "_calc_summary_roas", AsyncMock(return_value=None))
    monkeypatch.setattr(history_router, "_load_prev_period_totals", AsyncMock(return_value=None))

    await history_router.get_history_summary(
        date_from=today.replace(day=1).isoformat(),
        date_to=today.isoformat(),
        offer_code="DRC_CR2",
        offer_codes=None,
        campaign_name=None,
        db=AsyncMock(),
    )

    assert captured["offer_code"] == "DRC_CR2"
    assert captured["offer_codes"] == ["DRC_CR2"]


# Сценарий: _resolve_offer_codes объединяет offer_code и offer_codes без дублей.
def test_resolve_offer_codes_merges_and_dedupes():
    result = history_router._resolve_offer_codes("A", "b, A ,c")
    assert result == ["b", "A", "c"]

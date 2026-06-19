# -*- coding: utf-8 -*-
"""Автостарт: started → подтверждение, no_owner_ads → алерт; оба best-effort."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import apps.cabinet_scheduler.main as cab


# started → notify с числом объявлений
@pytest.mark.asyncio
async def test_started_confirms(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(cab, "notify_owners", spy)
    await cab._alert_autostart(
        object(),
        AsyncMock(),
        {"outcome": "started", "day": "2026-06-20", "ad_count": 7, "task_id": 42},
    )
    spy.assert_awaited_once()
    assert "7" in spy.await_args.kwargs["text"]


# no_owner_ads → алерт «кабинет не поднят»
@pytest.mark.asyncio
async def test_no_owner_ads_alerts(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(cab, "notify_owners", spy)
    await cab._alert_autostart(
        object(),
        AsyncMock(),
        {"outcome": "no_owner_ads", "day": "2026-06-20"},
    )
    spy.assert_awaited_once()
    assert (
        "не поднят" in spy.await_args.kwargs["text"].lower()
        or "не найдено" in spy.await_args.kwargs["text"].lower()
    )


# прочие outcome (already_done/scanning_paused) → молчим
@pytest.mark.asyncio
async def test_other_outcome_silent(monkeypatch):
    spy = AsyncMock()
    monkeypatch.setattr(cab, "notify_owners", spy)
    await cab._alert_autostart(object(), AsyncMock(), {"outcome": "already_done"})
    spy.assert_not_awaited()

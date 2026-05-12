# -*- coding: utf-8 -*-
"""Тесты API observer-настроек."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_get_observer_settings_returns_auto_enable_flag(mock_db):
    from apps.api.routers.settings import get_observer_settings

    row = SimpleNamespace(
        is_scanning_enabled=True,
        auto_enable_recommendations=True,
        pause_until=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    mock_db.execute = AsyncMock(return_value=result)

    response = await get_observer_settings(db=mock_db)

    assert response.auto_enable_recommendations is True


@pytest.mark.asyncio
async def test_update_observer_settings_round_trips_auto_enable_flag(mock_db):
    from apps.api.routers.settings import update_observer_settings
    from apps.api.schemas import ObserverSettingsSchema

    row = SimpleNamespace(
        singleton_key="default",
        interval_seconds=90,
        jitter_seconds=10,
        is_scanning_enabled=True,
        auto_enable_recommendations=False,
        pause_until=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    mock_db.execute = AsyncMock(return_value=result)

    response = await update_observer_settings(
        ObserverSettingsSchema(auto_enable_recommendations=True),
        db=mock_db,
    )

    assert row.auto_enable_recommendations is True
    assert response.auto_enable_recommendations is True
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_observer_settings_preserves_auto_enable_when_omitted(mock_db):
    from apps.api.routers.settings import update_observer_settings
    from apps.api.schemas import ObserverSettingsSchema

    row = SimpleNamespace(
        singleton_key="default",
        interval_seconds=90,
        jitter_seconds=10,
        is_scanning_enabled=True,
        auto_enable_recommendations=True,
        pause_until=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    mock_db.execute = AsyncMock(return_value=result)

    await update_observer_settings(
        ObserverSettingsSchema(is_scanning_enabled=False),
        db=mock_db,
    )

    assert row.auto_enable_recommendations is True

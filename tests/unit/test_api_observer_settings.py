# -*- coding: utf-8 -*-
"""Тесты API observer-настроек с раздельными step-level порогами."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_db():
    """Мок async DB-сессии для observer API."""
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


# Проверяем, что step-level payload сохраняет отдельные пороги и не схлопывается обратно в общий процент.
@pytest.mark.asyncio
async def test_update_observer_settings_persists_step_thresholds(mock_db):
    from apps.api.main import ObserverSettingsSchema, update_observer_settings

    row = SimpleNamespace(
        singleton_key="default",
        interval_seconds=90,
        jitter_seconds=10,
        is_scanning_enabled=False,
        warning_percent_of_stop=Decimal("80"),
        stop_percent_of_base=Decimal("100"),
        cpc_warning_percent_of_stop=Decimal("80"),
        cpc_stop_percent_of_base=Decimal("100"),
        cpl_warning_percent_of_stop=Decimal("80"),
        cpl_stop_percent_of_base=Decimal("100"),
        cpr_warning_percent_of_stop=Decimal("80"),
        cpr_stop_percent_of_base=Decimal("100"),
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    mock_db.execute = AsyncMock(return_value=result)

    response = await update_observer_settings(
        ObserverSettingsSchema(
            interval_seconds=120,
            jitter_seconds=5,
            is_scanning_enabled=False,
            cpc_warning_percent_of_stop=Decimal("90"),
            cpc_stop_percent_of_base=Decimal("85"),
            cpl_warning_percent_of_stop=Decimal("80"),
            cpl_stop_percent_of_base=Decimal("70"),
            cpr_warning_percent_of_stop=Decimal("65"),
            cpr_stop_percent_of_base=Decimal("60"),
        ),
        db=mock_db,
    )

    assert row.interval_seconds == 120
    assert row.jitter_seconds == 5
    assert row.warning_percent_of_stop == Decimal("65")
    assert row.stop_percent_of_base == Decimal("60")
    assert row.cpc_warning_percent_of_stop == Decimal("90")
    assert row.cpc_stop_percent_of_base == Decimal("85")
    assert row.cpl_warning_percent_of_stop == Decimal("80")
    assert row.cpl_stop_percent_of_base == Decimal("70")
    assert row.cpr_warning_percent_of_stop == Decimal("65")
    assert row.cpr_stop_percent_of_base == Decimal("60")
    assert response.cpc_warning_percent_of_stop == Decimal("90")
    assert response.cpl_stop_percent_of_base == Decimal("70")
    assert response.cpr_stop_percent_of_base == Decimal("60")
    mock_db.commit.assert_awaited_once()

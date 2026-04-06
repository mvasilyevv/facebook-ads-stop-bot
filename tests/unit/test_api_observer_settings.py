# -*- coding: utf-8 -*-
"""Тесты API observer-настроек с раздельными step-level порогами и валидацией B9."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError


@pytest.fixture
def mock_db():
    """Мок async DB-сессии для observer API."""
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


# Проверяем, что step-level payload сохраняет отдельные пороги.
@pytest.mark.asyncio
async def test_update_observer_settings_persists_step_thresholds(mock_db):
    from apps.api.routers.settings import update_observer_settings
    from apps.api.schemas import ObserverSettingsSchema

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


# Проверяем, что отрицательный stop_percent_of_base отклоняется валидатором (B9).
def test_observer_settings_schema_rejects_negative_stop_percent():
    from apps.api.schemas import ObserverSettingsSchema

    with pytest.raises(ValidationError) as exc_info:
        ObserverSettingsSchema(stop_percent_of_base=Decimal("-10"))
    errors = exc_info.value.errors()
    assert any(e["loc"] == ("stop_percent_of_base",) for e in errors)


# Проверяем, что нулевой stop_percent_of_base тоже отклоняется (должен быть > 0).
def test_observer_settings_schema_rejects_zero_stop_percent():
    from apps.api.schemas import ObserverSettingsSchema

    with pytest.raises(ValidationError) as exc_info:
        ObserverSettingsSchema(stop_percent_of_base=Decimal("0"))
    errors = exc_info.value.errors()
    assert any(e["loc"] == ("stop_percent_of_base",) for e in errors)


# Проверяем, что warning_percent_of_stop > 100 отклоняется.
def test_observer_settings_schema_rejects_warning_above_100():
    from apps.api.schemas import ObserverSettingsSchema

    with pytest.raises(ValidationError) as exc_info:
        ObserverSettingsSchema(warning_percent_of_stop=Decimal("150"))
    errors = exc_info.value.errors()
    assert any(e["loc"] == ("warning_percent_of_stop",) for e in errors)


# Проверяем, что корректные значения принимаются без ошибок.
def test_observer_settings_schema_accepts_valid_values():
    from apps.api.schemas import ObserverSettingsSchema

    schema = ObserverSettingsSchema(
        warning_percent_of_stop=Decimal("80"),
        stop_percent_of_base=Decimal("100"),
    )
    assert schema.stop_percent_of_base == Decimal("100")


# Проверяем, что cpc_stop_percent_of_base=None (отключено) проходит валидацию.
def test_observer_settings_schema_accepts_null_step_thresholds():
    from apps.api.schemas import ObserverSettingsSchema

    schema = ObserverSettingsSchema(
        cpc_stop_percent_of_base=None,
        cpl_stop_percent_of_base=None,
        cpr_stop_percent_of_base=None,
    )
    assert schema.cpc_stop_percent_of_base is None

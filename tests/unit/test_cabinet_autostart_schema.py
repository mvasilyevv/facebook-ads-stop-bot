# -*- coding: utf-8 -*-
"""Юнит-тесты схем автостарта кабинета (только расписание; кампании — в allowlist'е)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.api.routers.v1.schemas.cabinet_autostart import (
    CabinetAutostartPutRequest,
    CabinetAutostartResponse,
)


# Валидное расписание проходит
def test_put_valid_schedule() -> None:
    req = CabinetAutostartPutRequest(enabled=True, hour_utc=7, minute_utc=30)
    assert req.enabled is True
    assert req.hour_utc == 7
    assert req.minute_utc == 30


# Час вне 0..23 → ValidationError
def test_put_hour_out_of_range() -> None:
    with pytest.raises(ValidationError):
        CabinetAutostartPutRequest(enabled=True, hour_utc=24, minute_utc=0)


# Минута вне 0..59 → ValidationError
def test_put_minute_out_of_range() -> None:
    with pytest.raises(ValidationError):
        CabinetAutostartPutRequest(enabled=False, hour_utc=6, minute_utc=60)


# from_config берёт значения из dict, недостающие → дефолты
def test_response_from_config_defaults() -> None:
    resp = CabinetAutostartResponse.from_config({"enabled": True})
    assert resp.enabled is True
    assert resp.hour_utc == 6  # дефолт
    assert resp.minute_utc == 0

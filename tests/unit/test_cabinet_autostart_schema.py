# -*- coding: utf-8 -*-
"""Юнит-тесты схем автостарта кабинета (валидация + from_config). Без БД."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.api.routers.v1.schemas.cabinet_autostart import (
    CabinetAutostartPutRequest,
    CabinetAutostartResponse,
)


# Валидный конфиг проходит, даты нормализуются (трим + дедуп с сохранением порядка)
def test_put_valid_dates_dedup_and_trim() -> None:
    req = CabinetAutostartPutRequest(
        enabled=True,
        hour_utc=6,
        minute_utc=30,
        dates=[" 22.05 ", "25.05", "22.05"],
    )
    assert req.dates == ["22.05", "25.05"]


# Формат с годом (ДД.ММ.ГГ) допускается
def test_put_date_with_year_ok() -> None:
    req = CabinetAutostartPutRequest(enabled=True, hour_utc=0, minute_utc=0, dates=["25.03.26"])
    assert req.dates == ["25.03.26"]


# Кривая дата (не ДД.ММ) → ValidationError (ловим опечатку до сохранения)
def test_put_bad_date_rejected() -> None:
    with pytest.raises(ValidationError):
        CabinetAutostartPutRequest(enabled=True, hour_utc=6, minute_utc=0, dates=["май"])


# Час вне 0..23 → ValidationError
def test_put_hour_out_of_range() -> None:
    with pytest.raises(ValidationError):
        CabinetAutostartPutRequest(enabled=True, hour_utc=24, minute_utc=0, dates=[])


# Минута вне 0..59 → ValidationError
def test_put_minute_out_of_range() -> None:
    with pytest.raises(ValidationError):
        CabinetAutostartPutRequest(enabled=False, hour_utc=6, minute_utc=60, dates=[])


# Пустой список дат допустим (фича может быть включена без дат — ничего не включит)
def test_put_empty_dates_ok() -> None:
    req = CabinetAutostartPutRequest(enabled=True, hour_utc=6, minute_utc=0, dates=[])
    assert req.dates == []


# from_config берёт значения из dict, недостающие → дефолты
def test_response_from_config_defaults() -> None:
    resp = CabinetAutostartResponse.from_config({"enabled": True, "dates": ["22.05"]})
    assert resp.enabled is True
    assert resp.hour_utc == 6  # дефолт
    assert resp.minute_utc == 0
    assert resp.dates == ["22.05"]

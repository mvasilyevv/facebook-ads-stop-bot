# -*- coding: utf-8 -*-
"""Юнит-тесты схем автостарта кабинета (валидация + from_config). Без БД."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.api.routers.v1.schemas.cabinet_autostart import (
    CabinetAutostartPutRequest,
    CabinetAutostartResponse,
)


# Валидные campaign_ids проходят, нормализуются (трим + дедуп с сохранением порядка)
def test_put_valid_campaign_ids_dedup_and_trim() -> None:
    req = CabinetAutostartPutRequest(
        enabled=True,
        hour_utc=6,
        minute_utc=30,
        campaign_ids=[" 123456 ", "789", "123456"],
    )
    assert req.campaign_ids == ["123456", "789"]


# Нечисловой id (не Meta-ID) → ValidationError (ловим мусор до сохранения)
def test_put_bad_campaign_id_rejected() -> None:
    with pytest.raises(ValidationError):
        CabinetAutostartPutRequest(enabled=True, hour_utc=6, minute_utc=0, campaign_ids=["abc"])


# Час вне 0..23 → ValidationError
def test_put_hour_out_of_range() -> None:
    with pytest.raises(ValidationError):
        CabinetAutostartPutRequest(enabled=True, hour_utc=24, minute_utc=0, campaign_ids=[])


# Минута вне 0..59 → ValidationError
def test_put_minute_out_of_range() -> None:
    with pytest.raises(ValidationError):
        CabinetAutostartPutRequest(enabled=False, hour_utc=6, minute_utc=60, campaign_ids=[])


# Пустой список кампаний допустим (фича включена, но ничего не включит — безопасно)
def test_put_empty_campaigns_ok() -> None:
    req = CabinetAutostartPutRequest(enabled=True, hour_utc=6, minute_utc=0, campaign_ids=[])
    assert req.campaign_ids == []


# from_config берёт значения из dict, недостающие → дефолты
def test_response_from_config_defaults() -> None:
    resp = CabinetAutostartResponse.from_config({"enabled": True, "campaign_ids": ["123"]})
    assert resp.enabled is True
    assert resp.hour_utc == 6  # дефолт
    assert resp.minute_utc == 0
    assert resp.campaign_ids == ["123"]

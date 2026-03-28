# -*- coding: utf-8 -*-
"""Тесты логики суток кабинета."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from core.cabinet_day import (
    build_cabinet_day_archive_payload,
    has_any_metric_value,
    is_cabinet_day_reset_scan,
)


# Проверяем что полный нулевой скан считается началом новых суток кабинета
def test_is_cabinet_day_reset_scan_for_all_zero_metrics():
    rows = [
        {
            "spend": Decimal("0"),
            "clicks": 0,
            "leads": 0,
            "registrations": 0,
            "deposits": 0,
            "cpc": None,
            "cost_per_lead": None,
            "cost_per_registration": None,
        },
        {
            "spend": Decimal("0.00"),
            "clicks": 0,
            "leads": 0,
            "registrations": 0,
            "deposits": 0,
            "cpc": Decimal("0"),
            "cost_per_lead": None,
            "cost_per_registration": Decimal("0"),
        },
    ]

    assert is_cabinet_day_reset_scan(rows) is True


# Проверяем что любая ненулевая метрика отменяет срабатывание новых суток
def test_is_cabinet_day_reset_scan_ignores_non_zero_rows():
    rows = [
        {
            "spend": Decimal("0"),
            "clicks": 0,
            "leads": 0,
            "registrations": 0,
            "deposits": 0,
            "cpc": None,
            "cost_per_lead": None,
            "cost_per_registration": None,
        },
        {
            "spend": Decimal("1.25"),
            "clicks": 5,
            "leads": 1,
            "registrations": 0,
            "deposits": 0,
            "cpc": Decimal("0.25"),
            "cost_per_lead": Decimal("1.25"),
            "cost_per_registration": None,
        },
    ]

    assert is_cabinet_day_reset_scan(rows) is False
    assert has_any_metric_value(rows[1]) is True


# Проверяем что архив прошлых суток агрегируется по summary и кампаниям
def test_build_cabinet_day_archive_payload_groups_campaigns():
    snapshots = [
        SimpleNamespace(
            campaign_name="Campaign A",
            spend=Decimal("10.00"),
            clicks=20,
            leads=4,
            registrations=2,
            deposits=1,
            cpc=Decimal("0.50"),
            cost_per_lead=Decimal("2.50"),
            cost_per_registration=Decimal("5.00"),
        ),
        SimpleNamespace(
            campaign_name="Campaign B",
            spend=Decimal("5.50"),
            clicks=10,
            leads=2,
            registrations=1,
            deposits=0,
            cpc=Decimal("0.55"),
            cost_per_lead=Decimal("2.75"),
            cost_per_registration=Decimal("5.50"),
        ),
    ]

    summary, campaigns = build_cabinet_day_archive_payload(snapshots)

    assert summary["spend"] == "15.50"
    assert summary["clicks"] == 30
    assert summary["leads"] == 6
    assert summary["registrations"] == 3
    assert summary["deposits"] == 1
    assert summary["cpc"] == "0.5167"
    assert campaigns[0]["campaign"] == "Campaign A"
    assert campaigns[1]["campaign"] == "Campaign B"

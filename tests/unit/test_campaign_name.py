# -*- coding: utf-8 -*-
"""Тесты build_campaign_name — формат имени кампании."""

from __future__ import annotations

from core.campaign_creator.naming import build_campaign_name


# Сценарий: имя строится по шаблону MV | {GEO} | CR{N} | adset.pro | {date} | {N}.
def test_format_with_explicit_date():
    name = build_campaign_name(iter_num=2, geo_code="ke", date="13.05")
    assert name == "MV | KE | CR2 | adset.pro | 13.05 | 2"


# Сценарий: код гео приводится к верхнему регистру.
def test_geo_code_uppercased():
    name = build_campaign_name(iter_num=1, geo_code="ke", date="01.01")
    assert " | KE | " in name


# Сценарий: без даты используется завтрашняя в формате DD.MM (запуск на следующие сутки).
def test_default_date_tomorrow():
    from datetime import datetime, timedelta

    name = build_campaign_name(iter_num=5, geo_code="us")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d.%m")
    assert f" | {tomorrow} | 5" in name
    assert name.startswith("MV | US | CR5 | adset.pro | ")

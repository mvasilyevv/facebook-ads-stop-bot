# -*- coding: utf-8 -*-
"""Тесты build_campaign_name — формат имени кампании."""

from __future__ import annotations

from core.campaign_creator.naming import build_campaign_name


# Сценарий: имя строится по шаблону CR{N} | {OFFER} | MV | {date}.
def test_format_with_explicit_date():
    name = build_campaign_name(iter_num=2, offer_code="drc_cr2", date="25.03")
    assert name == "CR2 | DRC_CR2 | MV | 25.03"


# Сценарий: код оффера приводится к верхнему регистру.
def test_offer_code_uppercased():
    name = build_campaign_name(iter_num=1, offer_code="kenya", date="01.01")
    assert " | KENYA | " in name


# Сценарий: без даты используется сегодняшняя в формате DD.MM.
def test_default_date_today():
    from datetime import datetime

    name = build_campaign_name(iter_num=5, offer_code="x")
    today = datetime.now().strftime("%d.%m")
    assert name.endswith(f" | {today}")
    assert name.startswith("CR5 | X | MV | ")

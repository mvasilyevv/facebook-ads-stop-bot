# -*- coding: utf-8 -*-
"""Модели сканера: ScannedAdRow — нормализованная строка из Ads Manager."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(slots=True, frozen=True)
class ScannedAdRow:
    """Нормализованная строка объявления, полученная после сканирования."""

    fb_ad_id: str
    campaign_name: str
    adset_name: str
    ad_name: str
    delivery_status: str
    spend: Decimal
    clicks: int = 0
    cpc: Decimal | None = None
    outbound_clicks: int = 0
    outbound_ctr: Decimal | None = None
    landing_page_views: int = 0
    cost_per_landing_page_view: Decimal | None = None
    cpm: Decimal | None = None
    frequency: Decimal | None = None
    leads: int = 0
    cost_per_lead: Decimal | None = None
    registrations: int = 0
    cost_per_registration: Decimal | None = None
    deposits: int = 0
    resolved_offer_code: str | None = None

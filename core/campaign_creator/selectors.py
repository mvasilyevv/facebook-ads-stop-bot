# -*- coding: utf-8 -*-
"""Селекторы DOM-элементов Facebook Ads Manager для автосоздания кампании.

Использованы data-auto-logging-id — наиболее стабильные атрибуты FB UI.
"""

from __future__ import annotations

SELECTORS: dict[str, str] = {
    "campaign_name": '[data-auto-logging-id="fc22fd92f"]',
    "daily_budget": '[data-auto-logging-id="f4dd1c204"]',
    "adset_name": '[data-auto-logging-id="f6998cf82"]',
    "pixel": '[data-auto-logging-id="fbcbdc33b"]',
    "geo_search": '[data-auto-logging-id="fa15127b5"]',
    "ad_name": '[data-auto-logging-id="f5fa3b7ca"]',
    "headline": '[data-auto-logging-id="fc2a3b7c8"]',
    "landing_url": '[data-auto-logging-id="f47cca745"]',
    "url_params": '[data-auto-logging-id="f581d319d"]',
}

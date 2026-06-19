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
    budget: str = ""
    # Meta campaign.id (из am_tabular). Пусто, если скан не дал id (старый путь/DOM).
    campaign_id: str = ""
    reach: int = 0
    impressions: int = 0
    clicks: int = 0
    cpc: Decimal | None = None
    ctr: Decimal | None = None
    outbound_clicks: int = 0
    outbound_ctr: Decimal | None = None
    landing_page_views: int = 0
    cost_per_landing_page_view: Decimal | None = None
    cost_per_result: Decimal | None = None
    cpm: Decimal | None = None
    frequency: Decimal | None = None
    leads: int = 0
    cost_per_lead: Decimal | None = None
    registrations: int = 0
    cost_per_registration: Decimal | None = None
    deposits: int = 0
    resolved_offer_code: str | None = None
    # --- Волна 1: превью креатива (ad-level) + метаданные адсета (catalog-поля) ---
    # Не участвуют в стоп-правилах/FSM — только каталог + витрина. Пустая строка = нет данных.
    creative_thumb_url: str = ""  # превью крео (thumbnail_url), любой тип крео
    creative_image_url: str = ""  # полноразмер (image_url), только image-крео; пусто для видео
    adset_pixel_id: str = ""  # promoted_object.pixel_id адсета
    adset_daily_budget: str = ""  # daily_budget адсета (minor units, как отдаёт Meta)
    adset_lifetime_budget: str = ""  # lifetime_budget адсета
    adset_budget_remaining: str = ""  # budget_remaining адсета
    adset_learning_stage: str = ""  # learning_stage_info.status (LEARNING/LEARNING_LIMITED/"")

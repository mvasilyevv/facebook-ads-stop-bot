# -*- coding: utf-8 -*-
"""Frozen dataclasses для ответов Marketing API.

Все числовые поля из Meta приходят как строки — храним через Decimal.
Счётчики событий (impressions, clicks, leads) — int.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(slots=True, frozen=True)
class MetaAdAccount:
    """Рекламный кабинет из /me/adaccounts."""

    id: str  # "act_XXXXXXXXX"
    name: str
    currency: str  # ISO 4217, например "USD"
    timezone_name: str  # "Europe/Kiev"
    account_status: int  # 1=active, 2=disabled, 3=unsettled, 9=in_grace_period, 101=closed


@dataclass(slots=True, frozen=True)
class MetaCampaign:
    """Кампания из /act_X/campaigns."""

    id: str
    name: str
    status: str  # "ACTIVE", "PAUSED", "DELETED", "ARCHIVED"
    effective_status: str  # реальный статус с учётом родительского кабинета
    objective: str  # "OUTCOME_LEADS", "OUTCOME_SALES", "OUTCOME_TRAFFIC", ...
    daily_budget: Decimal | None  # в единицах валюты кабинета (центы), Decimal
    lifetime_budget: Decimal | None
    created_time: str  # ISO datetime
    updated_time: str


@dataclass(slots=True, frozen=True)
class MetaAdset:
    """Адсет из /act_X/adsets."""

    id: str
    campaign_id: str
    name: str
    status: str
    effective_status: str
    daily_budget: Decimal | None
    lifetime_budget: Decimal | None
    bid_amount: Decimal | None
    optimization_goal: str  # "OFFSITE_CONVERSIONS", "LEAD_GENERATION", ...


@dataclass(slots=True, frozen=True)
class MetaAd:
    """Объявление из /act_X/ads."""

    id: str
    adset_id: str
    campaign_id: str
    name: str
    status: str
    effective_status: str
    created_time: str


@dataclass(slots=True, frozen=True)
class MetaInsightsRow:
    """Строка из /insights endpoint.

    ВСЕ числовые поля из Meta приходят строками — всё денежное и rate храним как Decimal.
    Счётчики (impressions, clicks, leads, ...) — int, по умолчанию 0.
    """

    ad_id: str
    ad_name: str
    adset_name: str
    campaign_name: str
    spend: Decimal
    impressions: int
    clicks: int
    cpc: Decimal | None  # cost per click
    ctr: Decimal | None  # click-through rate в процентах
    cpm: Decimal | None
    frequency: Decimal | None
    reach: int | None
    outbound_clicks: int | None
    outbound_ctr: Decimal | None
    landing_page_views: int | None
    cost_per_landing_page_view: Decimal | None
    leads: int  # из actions[action_type=lead]
    cost_per_lead: Decimal | None
    registrations: int  # из actions[action_type=complete_registration]
    cost_per_registration: Decimal | None
    deposits: int  # из actions[action_type=purchase или кастомный ивент]
    cost_per_deposit: Decimal | None
    cost_per_result: Decimal | None
    date_start: str  # ISO date, например "2026-05-26"
    date_stop: str

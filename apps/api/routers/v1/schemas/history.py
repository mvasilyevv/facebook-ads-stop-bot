# -*- coding: utf-8 -*-
"""Pydantic-схемы для HistoryPage endpoints (Round 7.6).

6 endpoint'ов: summary, timeline, campaigns, events, offers, ads.
Все строятся на явной агрегации через raw SQL — от ORM не зависят напрямую.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

# ─────────────────────── Summary ────────────────────────────────────────────


class HistoryTotals(BaseModel):
    """Суммарные метрики за период."""

    model_config = ConfigDict(from_attributes=False)

    spend: str  # строка для точного Decimal
    impressions: int
    clicks: int
    leads: int
    registrations: int
    deposits: int
    active_ads_count: int


class HistoryRuleCount(BaseModel):
    """Количество срабатываний по конкретному правилу."""

    model_config = ConfigDict(from_attributes=False)

    rule_code: str
    count: int


class HistoryAlerts(BaseModel):
    """Сводка по алертам за период."""

    model_config = ConfigDict(from_attributes=False)

    warning_count: int
    stop_count: int
    by_rule: list[HistoryRuleCount]


class HistoryTasks(BaseModel):
    """Сводка по задачам (disable/enable) за период."""

    model_config = ConfigDict(from_attributes=False)

    disable_completed: int
    disable_failed: int
    enable_completed: int


class HistorySummaryOut(BaseModel):
    """Ответ GET /history/summary."""

    model_config = ConfigDict(from_attributes=False)

    from_iso: datetime
    to_iso: datetime
    totals: HistoryTotals
    alerts: HistoryAlerts
    tasks: HistoryTasks


# ─────────────────────── Timeline ───────────────────────────────────────────


class HistoryTimelineItem(BaseModel):
    """Элемент объединённой ленты alert + task."""

    model_config = ConfigDict(from_attributes=False)

    event_type: str  # "alert" | "task"
    ts: datetime
    fb_ad_id: str | None = None
    ad_name: str | None = None
    campaign_name: str | None = None
    # alert-поля
    stage: str | None = None
    rule_codes: list[str] | None = None
    # task-поля
    task_type: str | None = None
    task_status: str | None = None  # UPPERCASE через status_mapper


# ─────────────────────── Campaigns ──────────────────────────────────────────


class HistoryCampaignOut(BaseModel):
    """Строка /history/campaigns: суммы по кампании."""

    model_config = ConfigDict(from_attributes=False)

    campaign_id: str  # UUID → str
    fb_campaign_id: str | None = None
    campaign_name: str
    offer_code: str | None = None
    spend: str
    leads: int
    registrations: int
    deposits: int
    active_ads_count: int
    alerts_count: int
    cost_per_lead: str | None = None  # None если leads == 0


# ─────────────────────── Events ─────────────────────────────────────────────


class HistoryEventOut(BaseModel):
    """Строка /history/events: один AlertEvent с JOIN'ами."""

    model_config = ConfigDict(from_attributes=False)

    id: str  # UUID → str
    fb_ad_id: str
    ad_name: str
    campaign_name: str
    offer_code: str | None = None
    stage: str
    matched_rule_codes: list[str]
    created_at: datetime
    alert_payload: dict | None = None  # из metrics_json


# ─────────────────────── Offers ─────────────────────────────────────────────


class HistoryOfferOut(BaseModel):
    """Строка /history/offers: суммы по офферу."""

    model_config = ConfigDict(from_attributes=False)

    offer_id: str  # UUID → str
    offer_code: str
    offer_name: str
    spend: str
    leads: int
    registrations: int
    deposits: int
    alerts_count: int
    active_ads_count: int
    cost_per_lead: str | None = None


# ─────────────────────── Ads ────────────────────────────────────────────────


class HistoryAdOut(BaseModel):
    """Строка /history/ads: суммы по объявлению + последние события."""

    model_config = ConfigDict(from_attributes=False)

    fb_ad_id: str
    internal_id: str  # UUID → str
    ad_name: str
    campaign_name: str
    offer_code: str | None = None
    is_active: bool
    spend: str
    leads: int
    deposits: int
    last_alert_at: datetime | None = None
    last_alert_stage: str | None = None
    last_disable_at: datetime | None = None
    alerts_count_in_window: int

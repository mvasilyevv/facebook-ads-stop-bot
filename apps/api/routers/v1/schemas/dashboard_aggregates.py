# -*- coding: utf-8 -*-
"""Pydantic-схемы для dashboard-агрегационных endpoint'ов.

Покрывает /api/dashboard/stats, /batch, /spend-history, /chart-data, /performance.
Decimal во всех схемах сериализуется как str — стабильно и без потери точности.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DashboardStatsOut(BaseModel):
    """Сводные счётчики для overview-карточек DashboardPage.

    Все count-поля — int. observer_status строкой ('running'|'paused'|'unknown').
    last_scan_at — ISO-8601 datetime либо None если за последнюю неделю не было сканов.
    """

    model_config = ConfigDict(from_attributes=False)

    total_ads_monitored: int = 0
    ads_in_normal: int = 0
    ads_in_warning: int = 0
    ads_in_stop: int = 0
    ads_in_claimed: int = 0
    ads_in_disabled: int = 0
    active_incidents: int = 0
    # Волна 2/E: авторитетный спенд ТЕКУЩИХ суток кабинета с нуля (latest-per-ad с
    # полом по границе суток per-account TZ). None если не посчитан/ошибка — фронт
    # должен использовать это поле, а не сумму спенд-серии (та задваивает кумулятив).
    current_day_spend: str | None = None
    last_scan_at: datetime | None = None
    last_scan_outcome: str | None = None
    scans_today: int = 0
    scans_today_with_errors: int = 0
    observer_status: str = "unknown"
    pending_disable_tasks: int = 0
    pending_enable_tasks: int = 0
    failed_tasks_24h: int = 0


class DashboardBatchOut(BaseModel):
    """Композитный ответ /api/dashboard/batch — снижает количество fetch'ей на фронте.

    Контракт: даже если одна из секций упала, остальные возвращаются (см.
    partial-failure поведение в роутере). Списки могут быть пустыми.

    Секции:
    - recent_disable_tasks: задачи отключения (meta_api_mutation pause_ad / legacy disable)
    - recent_enable_tasks: задачи включения (meta_api_mutation activate_ad / legacy enable)
    """

    model_config = ConfigDict(from_attributes=False)

    stats: DashboardStatsOut
    recent_incidents: list[dict[str, Any]] = Field(default_factory=list)
    recent_alerts: list[dict[str, Any]] = Field(default_factory=list)
    recent_disable_tasks: list[dict[str, Any]] = Field(default_factory=list)
    recent_enable_tasks: list[dict[str, Any]] = Field(default_factory=list)
    enable_recommendations_pending: list[dict[str, Any]] = Field(default_factory=list)


class SpendPointOut(BaseModel):
    """Одна точка ad_metrics для /api/dashboard/spend-history (не бакетированная)."""

    model_config = ConfigDict(from_attributes=False)

    cycle_ts: datetime
    fb_ad_id: str | None = None
    spend: str | None = None
    impressions: int | None = None
    clicks: int | None = None
    leads: int | None = None
    registrations: int | None = None
    deposits: int | None = None


class ChartBucketOut(BaseModel):
    """Один бакет /api/dashboard/chart-data (по часам или дням).

    active_ads — COUNT DISTINCT ad_id в бакете.
    """

    model_config = ConfigDict(from_attributes=False)

    ts: datetime
    spend: str | None = None
    impressions: int | None = None
    clicks: int | None = None
    leads: int | None = None
    registrations: int | None = None
    deposits: int | None = None
    active_ads: int | None = None


class TopCampaignOut(BaseModel):
    """Строка топа кампаний по spend за окно ?days."""

    model_config = ConfigDict(from_attributes=False)

    campaign_id: str
    fb_campaign_id: str | None = None
    campaign_name: str
    spend: str | None = None
    leads: int | None = None
    deposits: int | None = None
    cost_per_lead: str | None = None
    active_ads_count: int = 0


class OfferLeaderboardRowOut(BaseModel):
    """Строка leaderboard'а офферов: метрики + count алертов."""

    model_config = ConfigDict(from_attributes=False)

    offer_id: str
    offer_code: str
    offer_name: str
    spend: str | None = None
    leads: int | None = None
    registrations: int | None = None
    deposits: int | None = None
    alerts_count: int = 0


class RuleViolationOut(BaseModel):
    """Топ правил по числу сработок (warning+stop) за окно ?days."""

    model_config = ConfigDict(from_attributes=False)

    rule_code: str
    count: int
    ads_count: int


class DashboardPerformanceOut(BaseModel):
    """Тяжёлая агрегация для секции Performance на DashboardPage."""

    model_config = ConfigDict(from_attributes=False)

    top_campaigns: list[TopCampaignOut] = Field(default_factory=list)
    offer_leaderboard: list[OfferLeaderboardRowOut] = Field(default_factory=list)
    top_rule_violations: list[RuleViolationOut] = Field(default_factory=list)


__all__ = [
    "DashboardStatsOut",
    "DashboardBatchOut",
    "SpendPointOut",
    "ChartBucketOut",
    "TopCampaignOut",
    "OfferLeaderboardRowOut",
    "RuleViolationOut",
    "DashboardPerformanceOut",
]

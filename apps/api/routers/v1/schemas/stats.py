# -*- coding: utf-8 -*-
"""Pydantic-схемы «Статистики залива» (/api/stats/*).

Money-поля (spend/cpc/.../revenue) — Decimal-строки (utils/serialize.decimal_str),
счётчики — int. None в производных = деление на ноль, фронт рисует «—».
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class FunnelTotalsOut(BaseModel):
    """Тоталы воронки Meta за окно (Decimal-строки для денег)."""

    spend: str | None = None
    impressions: int = 0
    clicks: int = 0
    leads: int = 0
    registrations: int = 0
    deposits: int = 0


class FunnelDerivedOut(BaseModel):
    """Производные метрики (None = знаменатель нулевой)."""

    cpc: str | None = None
    cpl: str | None = None
    cpr: str | None = None
    cpa: str | None = None
    ctr_pct: str | None = None
    cr_click_lead_pct: str | None = None
    cr_lead_reg_pct: str | None = None
    cr_reg_dep_pct: str | None = None


class HourlyPointOut(BaseModel):
    """Точка почасовой серии — ЧЕСТНАЯ дельта («сколько в этот час»)."""

    ts: datetime
    spend: str | None = None
    impressions: int = 0
    clicks: int = 0
    leads: int = 0
    registrations: int = 0
    deposits: int = 0
    active_ads: int = 0


class DailyPointOut(BaseModel):
    """Точка подневной серии — дневной итог (UTC-день)."""

    day: date
    spend: str | None = None
    impressions: int = 0
    clicks: int = 0
    leads: int = 0
    registrations: int = 0
    deposits: int = 0
    active_ads: int = 0


class MetaTodayBlockOut(BaseModel):
    """Блок Meta «за сегодня»: тоталы + производные + почасовые дельты."""

    totals: FunnelTotalsOut
    derived: FunnelDerivedOut
    series_hourly: list[HourlyPointOut] = Field(default_factory=list)


class MetaPeriodBlockOut(BaseModel):
    """Блок Meta «за период»: тоталы + производные + подневная серия."""

    totals: FunnelTotalsOut
    derived: FunnelDerivedOut
    series_daily: list[DailyPointOut] = Field(default_factory=list)


class TrackerTotalsOut(BaseModel):
    """Тоталы трекера AdSet.pro (UTC-дни)."""

    installs: int = 0
    registrations: int = 0
    ftds: int = 0
    deposits: int = 0
    confirmed_deposits: int = 0
    redeposits: int = 0
    revenue: str | None = None
    # ROI% = (revenue − spend Meta)/spend×100 — кросс-источник, см. attribution_note.
    roi_pct: str | None = None


class TrackerDailyPointOut(BaseModel):
    """Точка подневной серии трекера."""

    day: date
    installs: int = 0
    registrations: int = 0
    ftds: int = 0
    deposits: int = 0
    confirmed_deposits: int = 0
    redeposits: int = 0
    revenue: str | None = None


class TrackerBlockOut(BaseModel):
    """Блок трекера. available=false — данных нет/запрос упал (ответ не роняем)."""

    available: bool = False
    day_utc: date | None = None
    attribution_note: str = ""
    totals: TrackerTotalsOut = Field(default_factory=TrackerTotalsOut)
    series_daily: list[TrackerDailyPointOut] = Field(default_factory=list)
    unmatched_events: int = 0
    last_event_at: datetime | None = None
    processing_lag_ms: int | None = None
    data_quality: str = "unknown"
    backlog: int = 0
    duplicate_events: int = 0
    unsupported_events: int = 0
    reconciliation_drift: int | None = None
    materialization_drift: int | None = None


class BreakdownRowOut(BaseModel):
    """Строка разреза по офферу/кампании (за сегодня)."""

    key: str
    label: str
    spend: str | None = None
    clicks: int = 0
    leads: int = 0
    registrations: int = 0
    deposits: int = 0
    cpl: str | None = None


class StatsTodayOut(BaseModel):
    """Ответ GET /api/stats/today."""

    cabinet_day_start: datetime
    generated_at: datetime
    meta: MetaTodayBlockOut
    tracker: TrackerBlockOut
    breakdown: list[BreakdownRowOut] | None = None


class StatsPeriodOut(BaseModel):
    """Ответ GET /api/stats/period."""

    from_iso: datetime
    to_iso: datetime
    meta: MetaPeriodBlockOut
    tracker: TrackerBlockOut


__all__ = [
    "BreakdownRowOut",
    "DailyPointOut",
    "FunnelDerivedOut",
    "FunnelTotalsOut",
    "HourlyPointOut",
    "MetaPeriodBlockOut",
    "MetaTodayBlockOut",
    "StatsPeriodOut",
    "StatsTodayOut",
    "TrackerBlockOut",
    "TrackerDailyPointOut",
    "TrackerTotalsOut",
]

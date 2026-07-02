# -*- coding: utf-8 -*-
"""Роутер «Статистики залива» — воронка за сегодня и за период.

Endpoints (prefix /api от auto-discovery):
    GET /stats/today  — текущие сутки кабинета: тоталы + производные +
                        почасовые ЧЕСТНЫЕ дельты + блок трекера (+ breakdown).
    GET /stats/period — произвольный период (max 90д): тоталы + производные +
                        подневная серия + подневный блок трекера.

Тонкий слой: валидация окна + оркестрация core/dashboard/stats_queries.py +
производные из core/dashboard/stats_derived.py + маппинг в схемы.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from apps.api.deps import DepEngine, DepRedis
from apps.api.routers.v1.schemas.stats import (
    BreakdownRowOut,
    DailyPointOut,
    FunnelDerivedOut,
    FunnelTotalsOut,
    HourlyPointOut,
    MetaPeriodBlockOut,
    MetaTodayBlockOut,
    StatsPeriodOut,
    StatsTodayOut,
    TrackerBlockOut,
    TrackerDailyPointOut,
    TrackerTotalsOut,
)
from apps.api.utils.partition import default_window
from apps.api.utils.serialize import decimal_str
from core.dashboard import stats_queries as sq
from core.dashboard.stats_derived import compute_derived, compute_roi_pct, hourly_deltas

logger = logging.getLogger(__name__)

router = APIRouter(tags=["stats"])

# Лимиты окна периода — те же, что в history.py.
_MAX_RANGE_DAYS = 90
_DEFAULT_RANGE_HOURS = 720  # 30 дней

_ATTRIBUTION_NOTE = (
    "Трекер (AdSet.pro) считает по UTC-дню и postback-атрибуции — "
    "расхождение с метриками Meta (attribution gap) нормально."
)


def _parse_window(from_iso: str | None, to_iso: str | None) -> tuple[datetime, datetime]:
    """Окно периода: default 30 дней, максимум 90 (паттерн history._parse_window)."""
    if from_iso is None and to_iso is None:
        return default_window(hours=_DEFAULT_RANGE_HOURS)
    try:
        from_dt = (
            datetime.fromisoformat(from_iso)
            if from_iso
            else default_window(hours=_DEFAULT_RANGE_HOURS)[0]
        )
        to_dt = datetime.fromisoformat(to_iso) if to_iso else datetime.now(UTC)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"Неверный формат даты: {exc}") from exc
    if from_dt.tzinfo is None:
        from_dt = from_dt.replace(tzinfo=UTC)
    if to_dt.tzinfo is None:
        to_dt = to_dt.replace(tzinfo=UTC)
    if to_dt < from_dt:
        raise HTTPException(status_code=422, detail="to_iso должен быть >= from_iso")
    if (to_dt - from_dt) > timedelta(days=_MAX_RANGE_DAYS):
        raise HTTPException(
            status_code=422, detail=f"Диапазон не может превышать {_MAX_RANGE_DAYS} дней"
        )
    return from_dt, to_dt


def _totals_out(raw: dict[str, Any]) -> FunnelTotalsOut:
    return FunnelTotalsOut(
        spend=decimal_str(raw.get("spend")),
        impressions=int(raw.get("impressions") or 0),
        clicks=int(raw.get("clicks") or 0),
        leads=int(raw.get("leads") or 0),
        registrations=int(raw.get("registrations") or 0),
        deposits=int(raw.get("deposits") or 0),
    )


def _derived_out(totals_raw: dict[str, Any]) -> FunnelDerivedOut:
    derived = compute_derived(totals_raw)
    return FunnelDerivedOut(**{k: decimal_str(v) for k, v in derived.items()})


async def _tracker_block(
    engine: Any,
    *,
    day_from: Any,
    day_to: Any,
    meta_spend: Any,
    with_series: bool,
) -> TrackerBlockOut:
    """Блок трекера. Сбой запроса → available=false, основной ответ не роняем."""
    try:
        totals = await sq.fetch_tracker_totals(engine, day_from=day_from, day_to=day_to)
        series = (
            await sq.fetch_tracker_daily(engine, day_from=day_from, day_to=day_to)
            if with_series
            else []
        )
    except Exception:
        logger.exception("stats: блок трекера недоступен (day %s..%s)", day_from, day_to)
        return TrackerBlockOut(available=False, attribution_note=_ATTRIBUTION_NOTE)
    has_data = any(v for k, v in totals.items() if k != "revenue") or bool(totals.get("revenue"))
    return TrackerBlockOut(
        available=has_data,
        day_utc=day_to,
        attribution_note=_ATTRIBUTION_NOTE,
        totals=TrackerTotalsOut(
            installs=int(totals.get("installs") or 0),
            registrations=int(totals.get("registrations") or 0),
            deposits=int(totals.get("deposits") or 0),
            revenue=decimal_str(totals.get("revenue")),
            roi_pct=decimal_str(compute_roi_pct(totals.get("revenue"), meta_spend)),
        ),
        series_daily=[
            TrackerDailyPointOut(
                day=r["day"],
                installs=int(r.get("installs") or 0),
                registrations=int(r.get("registrations") or 0),
                deposits=int(r.get("deposits") or 0),
                revenue=decimal_str(r.get("revenue")),
            )
            for r in series
        ],
    )


# ─────────────────────── GET /stats/today ────────────────────────────────────


@router.get("/stats/today", response_model=StatsTodayOut)
async def get_stats_today(
    engine: DepEngine,
    redis: DepRedis,
    breakdown: str | None = Query(
        default=None, description="Разрез: offer | campaign (опционально)"
    ),
) -> StatsTodayOut:
    """Воронка текущих суток кабинета: тоталы, производные, почасовые дельты, трекер."""
    if breakdown is not None and breakdown not in sq.BREAKDOWN_GROUPS:
        raise HTTPException(
            status_code=422,
            detail=f"breakdown должен быть одним из: {sorted(sq.BREAKDOWN_GROUPS)}",
        )

    now = datetime.now(UTC)
    since = await sq.dominant_cabinet_day_start(engine, redis)

    totals_raw, hourly_rows = await asyncio.gather(
        sq.fetch_window_totals(engine, from_dt=since, to_dt=now),
        sq.fetch_hourly_snapshot_rows(engine, from_dt=since, to_dt=now),
    )
    tracker = await _tracker_block(
        engine,
        day_from=now.date(),
        day_to=now.date(),
        meta_spend=totals_raw.get("spend"),
        with_series=False,
    )

    breakdown_rows: list[BreakdownRowOut] | None = None
    if breakdown is not None:
        raw_rows = await sq.fetch_breakdown(engine, from_dt=since, to_dt=now, group=breakdown)
        breakdown_rows = [
            BreakdownRowOut(
                key=str(r["key"]),
                label=str(r["label"]),
                spend=decimal_str(r.get("spend")),
                clicks=int(r.get("clicks") or 0),
                leads=int(r.get("leads") or 0),
                registrations=int(r.get("registrations") or 0),
                deposits=int(r.get("deposits") or 0),
                cpl=decimal_str(compute_derived(r)["cpl"]),
            )
            for r in raw_rows
        ]

    return StatsTodayOut(
        cabinet_day_start=since,
        generated_at=now,
        meta=MetaTodayBlockOut(
            totals=_totals_out(totals_raw),
            derived=_derived_out(totals_raw),
            series_hourly=[
                HourlyPointOut(
                    ts=p["ts"],
                    spend=decimal_str(p["spend"]),
                    impressions=p["impressions"],
                    clicks=p["clicks"],
                    leads=p["leads"],
                    registrations=p["registrations"],
                    deposits=p["deposits"],
                    active_ads=p["active_ads"],
                )
                for p in hourly_deltas(hourly_rows)
            ],
        ),
        tracker=tracker,
        breakdown=breakdown_rows,
    )


# ─────────────────────── GET /stats/period ───────────────────────────────────


@router.get("/stats/period", response_model=StatsPeriodOut)
async def get_stats_period(
    engine: DepEngine,
    from_iso: str | None = Query(default=None, description="ISO-8601 начало периода"),
    to_iso: str | None = Query(default=None, description="ISO-8601 конец периода"),
) -> StatsPeriodOut:
    """Воронка за период (max 90д): тоталы, производные, подневные серии Meta и трекера."""
    from_dt, to_dt = _parse_window(from_iso, to_iso)

    totals_raw, daily_rows = await asyncio.gather(
        sq.fetch_period_totals(engine, from_dt=from_dt, to_dt=to_dt),
        sq.fetch_daily_series(engine, from_dt=from_dt, to_dt=to_dt),
    )
    tracker = await _tracker_block(
        engine,
        day_from=from_dt.date(),
        day_to=to_dt.date(),
        meta_spend=totals_raw.get("spend"),
        with_series=True,
    )

    return StatsPeriodOut(
        from_iso=from_dt,
        to_iso=to_dt,
        meta=MetaPeriodBlockOut(
            totals=_totals_out(totals_raw),
            derived=_derived_out(totals_raw),
            series_daily=[
                DailyPointOut(
                    day=r["day"],
                    spend=decimal_str(r.get("spend")),
                    impressions=int(r.get("impressions") or 0),
                    clicks=int(r.get("clicks") or 0),
                    leads=int(r.get("leads") or 0),
                    registrations=int(r.get("registrations") or 0),
                    deposits=int(r.get("deposits") or 0),
                    active_ads=int(r.get("active_ads") or 0),
                )
                for r in daily_rows
            ],
        ),
        tracker=tracker,
    )


__all__ = ["router"]

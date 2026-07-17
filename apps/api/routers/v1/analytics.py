"""Unified performance analytics for campaign -> adset -> ad drill-down."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query

from apps.api.deps import DepEngine, DepRedis
from apps.api.routers.v1.schemas.analytics import (
    AnalyticsDaypartOut,
    AnalyticsLiveBudgetSeriesOut,
    AnalyticsPerformanceOut,
)
from core.analytics.performance import (
    aggregate_performance,
    fetch_daypart_cells,
    fetch_filter_options,
    fetch_live_budget_points,
    fetch_performance_rows,
    fetch_source_quality,
)
from core.dashboard.cabinet_spend import cabinet_day_start_utc
from core.meta_api.account_tz import (
    DEFAULT_OFFSET_HOURS,
    active_account_ids,
    load_offset_map,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])

_MAX_RANGE_DAYS = 90
_CABINET_DAY_NOTE = (
    "Сегодня — сутки рекламного кабинета. Выбранный timezone меняет только отображение."
)


def _custom_window(from_iso: str | None, to_iso: str | None) -> tuple[datetime, datetime]:
    try:
        to_dt = datetime.fromisoformat(to_iso) if to_iso else datetime.now(UTC)
        from_dt = datetime.fromisoformat(from_iso) if from_iso else to_dt - timedelta(days=7)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Неверный формат даты: {exc}") from exc
    if from_dt.tzinfo is None:
        from_dt = from_dt.replace(tzinfo=UTC)
    if to_dt.tzinfo is None:
        to_dt = to_dt.replace(tzinfo=UTC)
    if to_dt < from_dt:
        raise HTTPException(status_code=422, detail="to_iso должен быть >= from_iso")
    if to_dt - from_dt > timedelta(days=_MAX_RANGE_DAYS):
        raise HTTPException(
            status_code=422,
            detail=f"Диапазон не может превышать {_MAX_RANGE_DAYS} дней",
        )
    return from_dt, to_dt


async def _resolve_window(
    *,
    engine,
    redis,
    period: Literal["today", "custom"],
    from_iso: str | None,
    to_iso: str | None,
) -> tuple[datetime, datetime, bool, dict[str, datetime] | None]:
    if period == "today":
        now = datetime.now(UTC)
        account_ids = await active_account_ids(engine)
        offsets = await load_offset_map(redis, account_ids) if account_ids else {}
        boundaries = {
            account_id: cabinet_day_start_utc(offset, now) for account_id, offset in offsets.items()
        }
        fallback = cabinet_day_start_utc(DEFAULT_OFFSET_HOURS, now)
        from_dt = min([fallback, *boundaries.values()])
        return from_dt, now, True, boundaries
    from_dt, to_dt = _custom_window(from_iso, to_iso)
    return from_dt, to_dt, False, None


@router.get("/performance", response_model=AnalyticsPerformanceOut)
async def get_analytics_performance(
    engine: DepEngine,
    redis: DepRedis,
    period: Literal["today", "custom"] = Query(default="today"),
    from_iso: str | None = Query(default=None),
    to_iso: str | None = Query(default=None),
    level: Literal["campaign", "adset", "ad"] = Query(default="campaign"),
    parent_id: uuid.UUID | None = Query(default=None),
    account_id: str | None = Query(default=None),
    offer_id: uuid.UUID | None = Query(default=None),
    campaign_id: uuid.UUID | None = Query(default=None),
    search: str | None = Query(default=None, max_length=200),
    sort: Literal[
        "name",
        "spend",
        "clicks",
        "registrations",
        "ftds",
        "confirmed_deposits",
        "revenue",
        "base_delta",
    ] = Query(default="spend"),
    direction: Literal["asc", "desc"] = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=200),
) -> AnalyticsPerformanceOut:
    """Return lossless performance metrics at one hierarchy level."""
    if level in {"adset", "ad"} and parent_id is None and campaign_id is None:
        raise HTTPException(
            status_code=422,
            detail="Для drill-down уровня adset/ad нужен parent_id или campaign_id",
        )
    from_dt, to_dt, is_live, cabinet_boundaries = await _resolve_window(
        engine=engine,
        redis=redis,
        period=period,
        from_iso=from_iso,
        to_iso=to_iso,
    )
    raw_rows, sources, filter_options = await asyncio.gather(
        fetch_performance_rows(
            engine,
            from_dt=from_dt,
            to_dt=to_dt,
            is_live=is_live,
            level=level,
            parent_id=parent_id,
            account_id=account_id,
            offer_id=offer_id,
            campaign_id=campaign_id,
            search=search,
            cabinet_boundaries=cabinet_boundaries,
        ),
        fetch_source_quality(engine, from_dt=from_dt, to_dt=to_dt),
        fetch_filter_options(engine),
    )
    payload = aggregate_performance(
        raw_rows,
        level=level,
        is_live=is_live,
        sort=sort,
        direction=direction,
        page=page,
        page_size=page_size,
    )
    return AnalyticsPerformanceOut(
        window={
            "from_iso": from_dt,
            "to_iso": to_dt,
            "is_live": is_live,
            "cabinet_day_note": _CABINET_DAY_NOTE if is_live else None,
        },
        sources=sources,
        filter_options=filter_options,
        **payload,
    )


@router.get("/live-budget", response_model=AnalyticsLiveBudgetSeriesOut)
async def get_analytics_live_budget(
    engine: DepEngine,
    redis: DepRedis,
    account_id: str | None = Query(default=None),
    offer_id: uuid.UUID | None = Query(default=None),
    campaign_id: uuid.UUID | None = Query(default=None),
) -> AnalyticsLiveBudgetSeriesOut:
    """Return hourly actual/base/stop series for the current cabinet day."""
    from_dt, to_dt, _, cabinet_boundaries = await _resolve_window(
        engine=engine,
        redis=redis,
        period="today",
        from_iso=None,
        to_iso=None,
    )
    points = await fetch_live_budget_points(
        engine,
        from_dt=from_dt,
        to_dt=to_dt,
        account_id=account_id,
        offer_id=offer_id,
        campaign_id=campaign_id,
        cabinet_boundaries=cabinet_boundaries,
    )
    return AnalyticsLiveBudgetSeriesOut(
        window={
            "from_iso": from_dt,
            "to_iso": to_dt,
            "is_live": True,
            "cabinet_day_note": _CABINET_DAY_NOTE,
        },
        points=points,
    )


@router.get("/daypart", response_model=AnalyticsDaypartOut)
async def get_analytics_daypart(
    engine: DepEngine,
    from_iso: str | None = Query(default=None),
    to_iso: str | None = Query(default=None),
    timezone: str = Query(default="UTC", min_length=1, max_length=64),
    account_id: str | None = Query(default=None),
    offer_id: uuid.UUID | None = Query(default=None),
    campaign_id: uuid.UUID | None = Query(default=None),
) -> AnalyticsDaypartOut:
    """Return weekday x hour cells in a validated IANA display timezone."""
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Неизвестный IANA timezone") from exc
    from_dt, to_dt = _custom_window(from_iso, to_iso)
    cells = await fetch_daypart_cells(
        engine,
        from_dt=from_dt,
        to_dt=to_dt,
        timezone_name=timezone,
        account_id=account_id,
        offer_id=offer_id,
        campaign_id=campaign_id,
    )
    return AnalyticsDaypartOut(
        timezone=timezone,
        from_iso=from_dt,
        to_iso=to_dt,
        cells=cells,
    )


__all__ = ["router"]

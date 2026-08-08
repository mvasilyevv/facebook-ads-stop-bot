"""Unified performance analytics for campaign -> adset -> ad drill-down."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from apps.api.deps import DepEngine, DepSettings
from apps.api.routers.v1.schemas.analytics import (
    AnalyticsDaypartOut,
    AnalyticsLiveBudgetSeriesOut,
    AnalyticsPerformanceOut,
)
from apps.api.routers.v1.schemas.operator import DataState, OperatorScopeEvidence
from core.analytics import DEFAULT_ANALYTICS_WINDOW
from core.analytics.performance import (
    aggregate_performance,
    fetch_daypart_cells,
    fetch_filter_options,
    fetch_live_budget_points,
    fetch_performance_rows,
    fetch_source_quality,
)
from core.meta_api.account_tz import (
    AccountCurrencyResolution,
    CabinetDayResolution,
    resolve_account_currencies,
    resolve_cabinet_days,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])

_MAX_RANGE_DAYS = 90
_CABINET_DAY_NOTE = (
    "Сегодня — сутки рекламного кабинета. Выбранный timezone меняет только отображение."
)
_TIMEZONE_ESTIMATE_ISSUE = (
    "Часовой пояс кабинета неизвестен; границы суток и суммы являются оценочными"
)
AnalyticsPeriod = Literal["today", "7d", "30d", "custom"]


@dataclass(frozen=True, slots=True)
class _ResolvedWindow:
    from_dt: datetime
    to_dt: datetime
    is_live: bool
    cabinet_boundaries: dict[str, datetime] | None
    cabinet_days: CabinetDayResolution
    extra_issues: tuple[str, ...] = ()

    @property
    def issues(self) -> list[str]:
        issues = list(self.extra_issues)
        if not self.cabinet_days.account_ids:
            issues.append("Не найден активный кабинет для определения часового пояса")
        if self.cabinet_days.missing_account_ids:
            issues.append(_TIMEZONE_ESTIMATE_ISSUE)
        return list(dict.fromkeys(issues))

    def response_payload(self) -> dict[str, object]:
        return {
            "from_iso": self.from_dt,
            "to_iso": self.to_dt,
            "is_live": self.is_live,
            "timezone": self.cabinet_days.cabinet_timezone,
            "timezone_known": self.cabinet_days.timezone_known,
            "timezone_state": self.cabinet_days.timezone_state,
            "missing_timezone_account_ids": list(self.cabinet_days.missing_account_ids),
            "issues": self.issues,
            "cabinet_day_note": _CABINET_DAY_NOTE if self.is_live else None,
        }


@dataclass(frozen=True, slots=True)
class _SectionQuality:
    state: DataState
    as_of: datetime | None
    freshness_seconds: int | None
    issues: list[str]


def _scope_evidence(
    *,
    cabinet_days: CabinetDayResolution,
    currencies: AccountCurrencyResolution,
    display_timezone: str,
) -> OperatorScopeEvidence:
    return OperatorScopeEvidence(
        account_ids=list(cabinet_days.account_ids),
        display_timezone=display_timezone,
        cabinet_timezone=cabinet_days.cabinet_timezone,
        cabinet_timezone_state=cabinet_days.timezone_state,
        missing_timezone_account_ids=list(cabinet_days.missing_account_ids),
        currency=currencies.currency,
        currency_state=currencies.state,
        missing_currency_account_ids=list(currencies.missing_account_ids),
        currency_observed_at=currencies.observed_at,
    )


_MONEY_METRICS = (
    "spend",
    "revenue",
    "cpc",
    "cost_per_registration",
    "cost_per_ftd",
    "roi_pct",
    "roas",
)


def _currency_issue(currencies: AccountCurrencyResolution) -> str | None:
    if currencies.state == "single":
        return None
    if currencies.state == "mixed":
        return "В выборке несколько валют; денежные суммы скрыты до выбора одного кабинета"
    missing = ", ".join(f"act_{value}" for value in currencies.missing_account_ids)
    return (
        f"Валюта не подтверждена для: {missing}"
        if missing
        else "Не найден кабинет с подтверждённой валютой"
    )


def _fail_closed_performance_money(
    payload: dict[str, object],
    *,
    currencies: AccountCurrencyResolution,
) -> None:
    """Remove every amount/derived ratio when the response has no single unit."""

    if currencies.state == "single":
        return
    totals = payload.get("totals")
    if isinstance(totals, dict):
        for key in _MONEY_METRICS:
            totals[key] = None
    payload["total_live_budget"] = None
    payload["total_budget_unavailable_reason"] = "Валюта выборки не подтверждена"
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in _MONEY_METRICS:
            row[key] = None
        row["live_budget"] = None
        row["budget_unavailable_reason"] = "Валюта строки не подтверждена"
        if row.get("state") not in {"unavailable", "stale"}:
            row["state"] = "partial"
        row_issues = row.get("issues")
        if isinstance(row_issues, list):
            row_issues.append("Денежные метрики скрыты: валюта не подтверждена")


def _section_quality(
    *,
    sources: dict[str, dict[str, object]],
    has_rows: bool,
    has_evidence: bool,
    partial_rows: bool = False,
    window_issues: list[str] | None = None,
) -> _SectionQuality:
    source_values = list(sources.values())
    timestamps = [
        value["last_event_at"]
        for value in source_values
        if isinstance(value.get("last_event_at"), datetime)
    ]
    as_of = min(timestamps) if timestamps else None
    lags = [
        int(value["lag_seconds"])
        for value in source_values
        if isinstance(value.get("lag_seconds"), int)
    ]
    freshness = max(lags) if lags else None
    issues = list(dict.fromkeys(window_issues or []))
    for value in source_values:
        if value.get("status") == "good":
            continue
        source_issues = value.get("issues")
        if isinstance(source_issues, list):
            issues.extend(str(issue) for issue in source_issues if issue)
        elif value.get("note"):
            issues.append(str(value["note"]))
    issues = list(dict.fromkeys(issues))
    statuses = {str(value.get("status")) for value in source_values}
    stale = any(
        value.get("status") == "degraded"
        and isinstance(value.get("lag_seconds"), int)
        and int(value["lag_seconds"]) > 900
        for value in source_values
    )
    if not has_evidence:
        state = (
            DataState.EMPTY
            if not has_rows and bool(statuses & {"good", "degraded"})
            else DataState.UNAVAILABLE
        )
    elif stale:
        state = DataState.STALE
    elif partial_rows or issues or statuses != {"good"}:
        state = DataState.PARTIAL
    else:
        state = DataState.READY
    return _SectionQuality(
        state=state,
        as_of=as_of,
        freshness_seconds=freshness,
        issues=issues,
    )


def _instant_window(
    from_iso: str | None,
    to_iso: str | None,
) -> tuple[datetime, datetime]:
    """Validate server-issued instants used only between analytics endpoints."""
    try:
        to_dt = datetime.fromisoformat(to_iso) if to_iso else datetime.now(UTC)
        from_dt = datetime.fromisoformat(from_iso) if from_iso else to_dt - DEFAULT_ANALYTICS_WINDOW
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Неверный формат даты") from exc
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


def _custom_calendar_window(
    *,
    from_date: date | None,
    to_date: date | None,
    cabinet_days: CabinetDayResolution,
) -> tuple[datetime, datetime, tuple[str, ...]]:
    """Resolve inclusive calendar dates on the server.

    One filtered cabinet uses its authoritative IANA timezone. A multi-cabinet
    view spans the earliest local start through the latest local end and marks
    the heterogeneous boundary explicitly instead of pretending it is UTC.
    """
    if from_date is None or to_date is None:
        raise HTTPException(
            status_code=422,
            detail="Для своего периода нужны from_date и to_date",
        )
    if to_date < from_date:
        raise HTTPException(status_code=422, detail="to_date должен быть >= from_date")
    inclusive_days = (to_date - from_date).days + 1
    if inclusive_days > _MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"Диапазон не может превышать {_MAX_RANGE_DAYS} дней",
        )

    timezone_names = sorted(set(cabinet_days.timezone_names.values()))
    zones = [ZoneInfo(name) for name in timezone_names] or [ZoneInfo("UTC")]
    starts = [datetime.combine(from_date, time.min, tzinfo=zone).astimezone(UTC) for zone in zones]
    ends = [
        datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
        for zone in zones
    ]
    extra_issues: tuple[str, ...] = ()
    if len(zones) > 1:
        extra_issues = (
            "Выбраны кабинеты с разными часовыми поясами; окно объединяет их локальные даты",
        )
    return min(starts), max(ends), extra_issues


async def _resolve_window(
    *,
    engine,
    period: AnalyticsPeriod,
    from_date: date | None,
    to_date: date | None,
    account_id: str | None = None,
) -> _ResolvedWindow:
    now = datetime.now(UTC)
    cabinet_days = await resolve_cabinet_days(
        engine,
        account_ids=[account_id] if account_id else None,
        now=now,
    )
    if period == "today":
        fallback = now.replace(hour=0, minute=0, second=0, microsecond=0)
        from_dt = min(cabinet_days.query_boundaries.values(), default=fallback)
        return _ResolvedWindow(
            from_dt=from_dt,
            to_dt=now,
            is_live=True,
            cabinet_boundaries=cabinet_days.query_boundaries,
            cabinet_days=cabinet_days,
        )
    if period in {"7d", "30d"}:
        days = 7 if period == "7d" else 30
        return _ResolvedWindow(
            from_dt=now - timedelta(days=days),
            to_dt=now,
            is_live=False,
            cabinet_boundaries=None,
            cabinet_days=cabinet_days,
        )
    from_dt, to_dt, extra_issues = _custom_calendar_window(
        from_date=from_date,
        to_date=to_date,
        cabinet_days=cabinet_days,
    )
    return _ResolvedWindow(
        from_dt=from_dt,
        to_dt=to_dt,
        is_live=False,
        cabinet_boundaries=None,
        cabinet_days=cabinet_days,
        extra_issues=extra_issues,
    )


@router.get("/performance", response_model=AnalyticsPerformanceOut)
async def get_analytics_performance(
    engine: DepEngine,
    settings: DepSettings,
    period: AnalyticsPeriod = Query(default="today"),
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
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
    resolved = await _resolve_window(
        engine=engine,
        period=period,
        from_date=from_date,
        to_date=to_date,
        account_id=account_id,
    )
    sources, filter_options, currencies = await asyncio.gather(
        fetch_source_quality(
            engine,
            from_dt=resolved.from_dt,
            to_dt=resolved.to_dt,
            cabinet_days=resolved.cabinet_days,
            account_id=account_id,
            offer_id=offer_id,
            campaign_id=campaign_id,
        ),
        fetch_filter_options(engine),
        resolve_account_currencies(
            engine,
            account_ids=list(resolved.cabinet_days.account_ids),
        ),
    )
    raw_rows = await fetch_performance_rows(
        engine,
        from_dt=resolved.from_dt,
        to_dt=resolved.to_dt,
        is_live=resolved.is_live,
        level=level,
        parent_id=parent_id,
        account_id=account_id,
        offer_id=offer_id,
        campaign_id=campaign_id,
        search=search,
        cabinet_boundaries=resolved.cabinet_boundaries,
        tracker_available=sources["tracker"]["status"] == "good",
    )
    truncated = len(raw_rows) > 50_000
    if truncated:
        raw_rows = raw_rows[:50_000]
    payload = aggregate_performance(
        raw_rows,
        level=level,
        is_live=resolved.is_live,
        sort=sort,
        direction=direction,
        page=page,
        page_size=page_size,
    )
    _fail_closed_performance_money(payload, currencies=currencies)
    quality_input = payload.pop("_quality")
    currency_issue = _currency_issue(currencies)
    quality = _section_quality(
        sources=sources,
        has_rows=bool(quality_input["has_rows"]),
        has_evidence=bool(quality_input["has_evidence"]),
        partial_rows=bool(quality_input["has_partial_rows"]),
        window_issues=[
            *resolved.issues,
            *([currency_issue] if currency_issue else []),
            *(["Выборка ограничена 50 000 объявлениями; уточните фильтры"] if truncated else []),
        ],
    )
    return AnalyticsPerformanceOut(
        state=quality.state,
        as_of=quality.as_of,
        freshness_seconds=quality.freshness_seconds,
        issues=quality.issues,
        scope=_scope_evidence(
            cabinet_days=resolved.cabinet_days,
            currencies=currencies,
            display_timezone=settings.app_timezone,
        ),
        window=resolved.response_payload(),
        sources=sources,
        filter_options=filter_options,
        **payload,
    )


@router.get("/live-budget", response_model=AnalyticsLiveBudgetSeriesOut)
async def get_analytics_live_budget(
    engine: DepEngine,
    settings: DepSettings,
    account_id: str | None = Query(default=None),
    offer_id: uuid.UUID | None = Query(default=None),
    campaign_id: uuid.UUID | None = Query(default=None),
) -> AnalyticsLiveBudgetSeriesOut:
    """Return hourly actual/base/stop series for the current cabinet day."""
    resolved = await _resolve_window(
        engine=engine,
        period="today",
        from_date=None,
        to_date=None,
        account_id=account_id,
    )
    points, sources, currencies = await asyncio.gather(
        fetch_live_budget_points(
            engine,
            from_dt=resolved.from_dt,
            to_dt=resolved.to_dt,
            account_id=account_id,
            offer_id=offer_id,
            campaign_id=campaign_id,
            cabinet_boundaries=resolved.cabinet_boundaries,
        ),
        fetch_source_quality(
            engine,
            from_dt=resolved.from_dt,
            to_dt=resolved.to_dt,
            cabinet_days=resolved.cabinet_days,
            account_id=account_id,
            offer_id=offer_id,
            campaign_id=campaign_id,
        ),
        resolve_account_currencies(
            engine,
            account_ids=list(resolved.cabinet_days.account_ids),
        ),
    )
    if sources["tracker"]["status"] != "good":
        for point in points:
            point["base"] = None
            point["stop"] = None
            point["unavailable_ads"] = int(point.get("unavailable_ads") or 0) + int(
                point.get("available_ads") or 0
            )
            point["available_ads"] = 0
    currency_issue = _currency_issue(currencies)
    if currency_issue is not None:
        for point in points:
            point["actual"] = None
            point["base"] = None
            point["stop"] = None
            point["unavailable_ads"] = int(point.get("unavailable_ads") or 0) + int(
                point.get("available_ads") or 0
            )
            point["available_ads"] = 0
    partial_points = any(
        point.get("actual") is None
        or point.get("base") is None
        or point.get("stop") is None
        or int(point.get("unavailable_ads") or 0) > 0
        for point in points
    )
    quality = _section_quality(
        sources=sources,
        has_rows=bool(points),
        has_evidence=bool(points),
        partial_rows=partial_points,
        window_issues=[
            *resolved.issues,
            *([currency_issue] if currency_issue else []),
            *(
                ["Не все объявления подтверждены в каждом почасовом Meta-снимке"]
                if partial_points
                else []
            ),
        ],
    )
    return AnalyticsLiveBudgetSeriesOut(
        state=quality.state,
        as_of=quality.as_of,
        freshness_seconds=quality.freshness_seconds,
        sources=sources,
        issues=quality.issues,
        scope=_scope_evidence(
            cabinet_days=resolved.cabinet_days,
            currencies=currencies,
            display_timezone=settings.app_timezone,
        ),
        window=resolved.response_payload(),
        points=points,
    )


@router.get("/daypart", response_model=AnalyticsDaypartOut)
async def get_analytics_daypart(
    engine: DepEngine,
    settings: DepSettings,
    from_iso: str | None = Query(default=None),
    to_iso: str | None = Query(default=None),
    account_id: str | None = Query(default=None),
    offer_id: uuid.UUID | None = Query(default=None),
    campaign_id: uuid.UUID | None = Query(default=None),
) -> AnalyticsDaypartOut:
    """Return weekday x hour cells in the server-owned display timezone."""
    timezone = settings.app_timezone
    from_dt, to_dt = _instant_window(from_iso, to_iso)
    cabinet_days = await resolve_cabinet_days(
        engine,
        account_ids=[account_id] if account_id else None,
        now=min(datetime.now(UTC), to_dt),
    )
    sources, currencies = await asyncio.gather(
        fetch_source_quality(
            engine,
            from_dt=from_dt,
            to_dt=to_dt,
            cabinet_days=cabinet_days,
            account_id=account_id,
            offer_id=offer_id,
            campaign_id=campaign_id,
        ),
        resolve_account_currencies(
            engine,
            account_ids=list(cabinet_days.account_ids),
        ),
    )
    cells = await fetch_daypart_cells(
        engine,
        from_dt=from_dt,
        to_dt=to_dt,
        timezone_name=timezone,
        account_id=account_id,
        offer_id=offer_id,
        campaign_id=campaign_id,
        tracker_available=sources["tracker"]["status"] == "good",
    )
    partial_cells = any(
        cell.get("clicks") is None or cell.get("registrations") is None or cell.get("ftds") is None
        for cell in cells
    )
    quality = _section_quality(
        sources=sources,
        has_rows=bool(cells),
        has_evidence=bool(cells),
        partial_rows=partial_cells,
        window_issues=(
            ["Не все почасовые интервалы подтверждены обоими источниками"] if partial_cells else []
        ),
    )
    return AnalyticsDaypartOut(
        state=quality.state,
        as_of=quality.as_of,
        freshness_seconds=quality.freshness_seconds,
        sources=sources,
        issues=quality.issues,
        scope=_scope_evidence(
            cabinet_days=cabinet_days,
            currencies=currencies,
            display_timezone=timezone,
        ),
        timezone=timezone,
        from_iso=from_dt,
        to_iso=to_dt,
        cells=cells,
    )


__all__ = ["router"]

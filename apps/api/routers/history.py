# -*- coding: utf-8 -*-
"""FastAPI роутер для страницы «История заливов»."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_db
from apps.api.schemas import (
    HistoryAdRow,
    HistoryCampaignRow,
    HistoryEventItem,
    HistoryEventsPage,
    HistoryOfferSummary,
    HistorySummarySchema,
    HistoryTimelinePoint,
)
from core.domain import DisableTaskStatus
from core.fake_deposits import (
    load_fake_deposits_by_campaign as _load_fake_deposits_by_campaign,
)
from core.fake_deposits import (
    load_fake_deposits_by_offer as _load_fake_deposits_by_offer,
)
from core.fake_deposits import (
    load_total_fake_deposits as _load_total_fake_deposits,
)
from core.math_utils import safe_div as _safe_div
from core.models import (
    AdSnapshot,
    AlertEvent,
    CabinetDayArchive,
    DisableTask,
    EnableTask,
    Offer,
)
from core.settings_queries import get_observer_settings as _load_observer_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["history"])

# ------------------------------------------------------------------
# Вспомогательные функции
# ------------------------------------------------------------------


def _parse_iso_date(value: str) -> date:
    """Парсит ISO-дату (YYYY-MM-DD)."""
    return date.fromisoformat(value)


def _date_to_datetime(d: date) -> datetime:
    """Конвертирует date в datetime (начало дня UTC)."""
    return datetime(d.year, d.month, d.day)


async def _load_campaigns_for_offer(
    db: AsyncSession,
    offer_code: str,
) -> set[str]:
    """Возвращает имена кампаний, привязанных к офферу через AdSnapshot."""
    q = (
        select(AdSnapshot.campaign_name)
        .where(func.lower(AdSnapshot.resolved_offer_code) == offer_code.lower())
        .distinct()
    )
    result = await db.execute(q)
    return {row[0] for row in result.all()}


def _filter_campaigns_by_offer(
    campaigns: list[dict],
    offer_campaigns: set[str],
) -> list[dict]:
    """Фильтрует кампании по набору имён из AdSnapshot."""
    return [c for c in campaigns if c.get("campaign") in offer_campaigns]


def _filter_campaigns_by_name(
    campaigns: list[dict],
    name: str,
) -> list[dict]:
    """Фильтрует список кампаний по имени кампании (exact match)."""
    return [c for c in campaigns if c.get("campaign") == name]


def _apply_campaign_filters(
    campaigns: list[dict],
    offer_campaigns: set[str] | None,
    campaign_name: str | None,
) -> list[dict]:
    """Применяет фильтры по офферу и имени кампании."""
    if offer_campaigns is not None:
        campaigns = _filter_campaigns_by_offer(campaigns, offer_campaigns)
    if campaign_name:
        campaigns = _filter_campaigns_by_name(campaigns, campaign_name)
    return campaigns


def _sum_campaign_metrics(campaigns: list[dict]) -> dict:
    """Суммирует метрики из списка кампаний."""
    totals: dict = {
        "spend": Decimal("0"),
        "clicks": 0,
        "leads": 0,
        "regs": 0,
        "deps": 0,
    }
    for c in campaigns:
        totals["spend"] += Decimal(str(c.get("spend", 0)))
        totals["clicks"] += int(c.get("clicks", 0))
        totals["leads"] += int(c.get("leads", 0))
        totals["regs"] += int(c.get("registrations", 0))
        totals["deps"] += int(c.get("deposits", 0))
    return totals


# ------------------------------------------------------------------
# GET /history/summary
# ------------------------------------------------------------------


async def _count_alerts_in_range(
    db: AsyncSession,
    dt_from: datetime,
    dt_to: datetime,
    offer_code: str | None,
    campaign_name: str | None = None,
) -> tuple[int, int]:
    """Считает общее кол-во алертов и стопов за период.

    Returns:
        (total_alerts, total_stops)
    """
    q = select(func.count(), AlertEvent.stage).where(
        and_(
            AlertEvent.created_at >= dt_from,
            AlertEvent.created_at < dt_to,
        )
    )
    # JOIN к AdSnapshot нужен при фильтре по офферу или кампании
    if offer_code or campaign_name:
        q = q.join(AdSnapshot, AlertEvent.ad_id == AdSnapshot.ad_id)
        if offer_code:
            q = q.where(func.lower(AdSnapshot.resolved_offer_code) == offer_code.lower())
        if campaign_name:
            q = q.where(AdSnapshot.campaign_name == campaign_name)
    q = q.group_by(AlertEvent.stage)
    rows = (await db.execute(q)).all()

    total = 0
    stops = 0
    for cnt, stage in rows:
        total += cnt
        if stage and stage.value == "STOP":
            stops += cnt
    return total, stops


async def _count_disables_in_range(
    db: AsyncSession,
    dt_from: datetime,
    dt_to: datetime,
    offer_code: str | None,
    campaign_name: str | None = None,
) -> int:
    """Считает успешные отключения за период."""
    q = select(func.count()).where(
        and_(
            DisableTask.created_at >= dt_from,
            DisableTask.created_at < dt_to,
            DisableTask.status == DisableTaskStatus.SUCCEEDED,
        )
    )
    if offer_code or campaign_name:
        q = q.join(AdSnapshot, DisableTask.ad_id == AdSnapshot.ad_id)
        if offer_code:
            q = q.where(func.lower(AdSnapshot.resolved_offer_code) == offer_code.lower())
        if campaign_name:
            q = q.where(AdSnapshot.campaign_name == campaign_name)
    result = await db.execute(q)
    return result.scalar() or 0


@router.get("/history/summary", response_model=HistorySummarySchema)
async def get_history_summary(
    date_from: str = Query(..., description="ISO дата начала"),
    date_to: str = Query(..., description="ISO дата конца"),
    offer_code: str | None = Query(None),
    campaign_name: str | None = Query(None, description="Фильтр по кампании"),
    db: AsyncSession = Depends(get_db),
) -> HistorySummarySchema:
    """Агрегированные метрики за период с дельтами предыдущего периода."""
    d_from = _parse_iso_date(date_from)
    d_to = _parse_iso_date(date_to)
    dt_from = _date_to_datetime(d_from)
    dt_to = _date_to_datetime(d_to + timedelta(days=1))

    # Загружаем кампании оффера, если указан offer_code
    offer_campaigns: set[str] | None = None
    if offer_code:
        offer_campaigns = await _load_campaigns_for_offer(db, offer_code)

    archives = await _load_archives(db, dt_from, dt_to)
    totals = _aggregate_archives(archives, offer_campaigns, campaign_name)

    # Добавляем live данные для «сегодня»
    today = date.today()
    if d_from <= today <= d_to:
        live = await _load_live_ads_for_today(db, offer_campaigns, campaign_name)
        for data in live.values():
            totals["spend"] += data["spend"]
            totals["clicks"] += data["clicks"]
            totals["leads"] += data["leads"]
            totals["regs"] += data["regs"]
            totals["deps"] += data["deps"]

    total_alerts, total_stops = await _count_alerts_in_range(
        db, dt_from, dt_to, offer_code, campaign_name
    )
    total_disables = await _count_disables_in_range(
        db,
        dt_from,
        dt_to,
        offer_code,
        campaign_name,
    )

    # Корректировка ложных депозитов
    total_fake = await _load_total_fake_deposits(db)
    if total_fake > 0:
        totals["deps"] = max(0, totals["deps"] - total_fake)

    # ROAS: revenue = deps × средний CPA по офферам
    roas = await _calc_summary_roas(db, archives, totals, offer_code)

    schema = _build_summary_schema(
        d_from,
        d_to,
        totals,
        len(archives),
        total_alerts,
        total_stops,
        total_disables,
        roas=roas,
    )

    # Дельты предыдущего периода
    prev_totals = await _load_prev_period_totals(
        db,
        d_from,
        d_to,
        offer_campaigns,
        campaign_name,
    )
    if prev_totals:
        schema.prev_spend = prev_totals["spend"]
        schema.prev_leads = prev_totals["leads"]
        schema.prev_registrations = prev_totals["regs"]
        schema.prev_deposits = prev_totals["deps"]

    return schema


async def _load_archives(
    db: AsyncSession,
    dt_from: datetime,
    dt_to: datetime,
) -> list[CabinetDayArchive]:
    """Загружает архивы за период."""
    q = (
        select(CabinetDayArchive)
        .where(
            and_(
                CabinetDayArchive.started_at >= dt_from,
                CabinetDayArchive.ended_at <= dt_to,
            )
        )
        .order_by(CabinetDayArchive.started_at)
    )
    result = await db.execute(q)
    return list(result.scalars().all())


async def _load_prev_period_totals(
    db: AsyncSession,
    d_from: date,
    d_to: date,
    offer_campaigns: set[str] | None,
    campaign_name: str | None = None,
) -> dict | None:
    """Загружает агрегаты предыдущего периода той же длины."""
    period_days = (d_to - d_from).days + 1
    prev_from = d_from - timedelta(days=period_days)
    prev_to = d_from - timedelta(days=1)
    prev_dt_from = _date_to_datetime(prev_from)
    prev_dt_to = _date_to_datetime(prev_to + timedelta(days=1))

    prev_archives = await _load_archives(db, prev_dt_from, prev_dt_to)
    if not prev_archives:
        return None
    return _aggregate_archives(prev_archives, offer_campaigns, campaign_name)


def _aggregate_archives(
    archives: list[CabinetDayArchive],
    offer_campaigns: set[str] | None,
    campaign_name: str | None = None,
) -> dict:
    """Агрегирует метрики из архивов, опционально фильтруя по офферу/кампании."""
    totals: dict = {
        "spend": Decimal("0"),
        "clicks": 0,
        "leads": 0,
        "regs": 0,
        "deps": 0,
    }
    for arch in archives:
        # Если есть любой фильтр — работаем через campaigns_json
        if offer_campaigns is not None or campaign_name:
            filtered = _apply_campaign_filters(
                arch.campaigns_json or [],
                offer_campaigns,
                campaign_name,
            )
            m = _sum_campaign_metrics(filtered)
        else:
            s = arch.summary_json or {}
            m = {
                "spend": Decimal(str(s.get("spend", 0))),
                "clicks": int(s.get("clicks", 0)),
                "leads": int(s.get("leads", 0)),
                "regs": int(s.get("registrations", 0)),
                "deps": int(s.get("deposits", 0)),
            }
        for k in totals:
            totals[k] += m[k]  # type: ignore[operator]
    return totals


async def _calc_summary_roas(
    db: AsyncSession,
    archives: list[CabinetDayArchive],
    totals: dict,
    offer_code: str | None,
) -> Decimal | None:
    """Считает ROAS для summary: revenue (deps × CPA) / spend."""
    spend = totals["spend"]
    if not spend:
        return None
    campaign_offer_map = await _load_campaign_offer_map(db)
    grouped = _group_by_offer_code(archives, campaign_offer_map)
    if offer_code:
        grouped = {k: v for k, v in grouped.items() if k.upper() == offer_code.upper()}
    codes = list(grouped.keys())
    offers_map = await _load_offers_map(db, codes)
    # Используем deps из totals (уже скорректированные), распределяя пропорционально
    revenue = Decimal("0")
    for code, metrics in grouped.items():
        offer = offers_map.get(code.upper())
        cpa = offer.cpa_amount if offer else Decimal("0")
        deps = metrics["deps"]
        revenue += Decimal(str(deps)) * cpa
    if not revenue:
        return Decimal("0")
    return (revenue / spend).quantize(Decimal("0.01"))


def _build_summary_schema(
    d_from: date,
    d_to: date,
    totals: dict,
    days_count: int,
    total_alerts: int,
    total_stops: int,
    total_disables: int,
    roas: Decimal | None = None,
) -> HistorySummarySchema:
    """Строит HistorySummarySchema из агрегатов."""
    spend = totals["spend"]
    clicks = totals["clicks"]
    leads = totals["leads"]
    regs = totals["regs"]
    deps = totals["deps"]

    return HistorySummarySchema(
        date_from=d_from.isoformat(),
        date_to=d_to.isoformat(),
        days_count=days_count,
        total_spend=spend,
        total_clicks=clicks,
        total_leads=leads,
        total_registrations=regs,
        total_deposits=deps,
        avg_cpc=_safe_div(spend, clicks),
        avg_cpl=_safe_div(spend, leads),
        avg_cpr=_safe_div(spend, regs),
        avg_spend_per_dep=_safe_div(spend, deps),
        roas=roas,
        total_alerts=total_alerts,
        total_stops=total_stops,
        total_disables=total_disables,
    )


# ------------------------------------------------------------------
# GET /history/timeline
# ------------------------------------------------------------------


def _archive_to_timeline_point(
    arch: CabinetDayArchive,
    offer_campaigns: set[str] | None,
    campaign_name: str | None = None,
) -> HistoryTimelinePoint:
    """Конвертирует один архив в точку таймлайна."""
    if offer_campaigns is not None or campaign_name:
        filtered = _apply_campaign_filters(
            arch.campaigns_json or [],
            offer_campaigns,
            campaign_name,
        )
        m = _sum_campaign_metrics(filtered)
    else:
        s = arch.summary_json or {}
        m = {
            "spend": Decimal(str(s.get("spend", 0))),
            "clicks": int(s.get("clicks", 0)),
            "leads": int(s.get("leads", 0)),
            "regs": int(s.get("regs", 0)),
            "deps": int(s.get("deps", 0)),
        }

    spend = m["spend"]
    return HistoryTimelinePoint(
        date=arch.started_at.date().isoformat(),
        spend=spend,
        clicks=m["clicks"],
        leads=m["leads"],
        registrations=m["regs"],
        deposits=m["deps"],
        cpc=_safe_div(spend, m["clicks"]),
        cpl=_safe_div(spend, m["leads"]),
        cpr=_safe_div(spend, m["regs"]),
        spend_per_dep=_safe_div(spend, m["deps"]),
    )


@router.get("/history/timeline", response_model=list[HistoryTimelinePoint])
async def get_history_timeline(
    date_from: str = Query(..., description="ISO дата начала"),
    date_to: str = Query(..., description="ISO дата конца"),
    offer_code: str | None = Query(None),
    campaign_name: str | None = Query(None, description="Фильтр по кампании"),
    db: AsyncSession = Depends(get_db),
) -> list[HistoryTimelinePoint]:
    """Таймлайн метрик по дням за период."""
    d_from = _parse_iso_date(date_from)
    d_to = _parse_iso_date(date_to)
    dt_from = _date_to_datetime(d_from)
    dt_to = _date_to_datetime(d_to + timedelta(days=1))

    # Загружаем кампании оффера, если указан offer_code
    offer_campaigns: set[str] | None = None
    if offer_code:
        offer_campaigns = await _load_campaigns_for_offer(db, offer_code)

    archives = await _load_archives(db, dt_from, dt_to)
    return [_archive_to_timeline_point(arch, offer_campaigns, campaign_name) for arch in archives]


# ------------------------------------------------------------------
# GET /history/campaigns
# ------------------------------------------------------------------

_CAMPAIGN_SORT_KEYS = {
    "spend": "total_spend",
    "clicks": "total_clicks",
    "leads": "total_leads",
    "registrations": "total_registrations",
    "deposits": "total_deposits",
    "cpl": "avg_cpl",
    "cpr": "avg_cpr",
    "alerts": "alerts_count",
    "disables": "disables_count",
}


def _group_campaigns_from_archives(
    archives: list[CabinetDayArchive],
    offer_campaigns: set[str] | None,
    campaign_name: str | None = None,
) -> dict[str, dict]:
    """Группирует кампании из архивов по campaign name."""
    grouped: dict[str, dict] = {}
    for arch in archives:
        campaigns = _apply_campaign_filters(
            arch.campaigns_json or [],
            offer_campaigns,
            campaign_name,
        )
        for c in campaigns:
            name = c.get("campaign", "")
            if name not in grouped:
                grouped[name] = {
                    "spend": Decimal("0"),
                    "clicks": 0,
                    "leads": 0,
                    "regs": 0,
                    "deps": 0,
                }
            g = grouped[name]
            g["spend"] += Decimal(str(c.get("spend", 0)))
            g["clicks"] += int(c.get("clicks", 0))
            g["leads"] += int(c.get("leads", 0))
            g["regs"] += int(c.get("registrations", 0))
            g["deps"] += int(c.get("deposits", 0))
    return grouped


async def _count_campaign_events(
    db: AsyncSession,
    dt_from: datetime,
    dt_to: datetime,
    campaign_names: list[str],
) -> tuple[dict[str, int], dict[str, int]]:
    """Считает алерты и отключения по кампаниям.

    Returns:
        (alerts_by_campaign, disables_by_campaign)
    """
    if not campaign_names:
        return {}, {}

    # Алерты по кампаниям
    alerts_q = (
        select(AdSnapshot.campaign_name, func.count())
        .join(AlertEvent, AlertEvent.ad_id == AdSnapshot.ad_id)
        .where(
            and_(
                AlertEvent.created_at >= dt_from,
                AlertEvent.created_at < dt_to,
                AdSnapshot.campaign_name.in_(campaign_names),
            )
        )
        .group_by(AdSnapshot.campaign_name)
    )
    alerts_rows = (await db.execute(alerts_q)).all()
    alerts_map = {name: cnt for name, cnt in alerts_rows}

    # Отключения по кампаниям
    disables_q = (
        select(AdSnapshot.campaign_name, func.count())
        .join(DisableTask, DisableTask.ad_id == AdSnapshot.ad_id)
        .where(
            and_(
                DisableTask.created_at >= dt_from,
                DisableTask.created_at < dt_to,
                DisableTask.status == DisableTaskStatus.SUCCEEDED,
                AdSnapshot.campaign_name.in_(campaign_names),
            )
        )
        .group_by(AdSnapshot.campaign_name)
    )
    disables_rows = (await db.execute(disables_q)).all()
    disables_map = {name: cnt for name, cnt in disables_rows}

    return alerts_map, disables_map


def _build_campaign_row(
    name: str,
    data: dict,
    alerts_count: int,
    disables_count: int,
    fake_by_campaign: dict[str, int] | None = None,
) -> HistoryCampaignRow:
    """Строит строку таблицы кампаний."""
    spend = data["spend"]
    raw_deps = data["deps"]
    fake_count = (fake_by_campaign or {}).get(name, 0)
    effective_deps = max(0, raw_deps - fake_count)
    return HistoryCampaignRow(
        campaign_name=name,
        offer_code=None,
        total_spend=spend,
        total_clicks=data["clicks"],
        total_leads=data["leads"],
        total_registrations=data["regs"],
        total_deposits=effective_deps,
        avg_cpl=_safe_div(spend, data["leads"]),
        avg_cpr=_safe_div(spend, data["regs"]),
        avg_spend_per_dep=_safe_div(spend, effective_deps),
        roas=None,
        alerts_count=alerts_count,
        disables_count=disables_count,
    )


def _sort_campaign_rows(
    rows: list[HistoryCampaignRow],
    sort_by: str,
    sort_dir: str,
) -> list[HistoryCampaignRow]:
    """Сортирует строки кампаний."""
    attr = _CAMPAIGN_SORT_KEYS.get(sort_by, "total_spend")
    reverse = sort_dir.lower() == "desc"
    return sorted(
        rows,
        key=lambda r: getattr(r, attr, 0) or 0,
        reverse=reverse,
    )


@router.get("/history/campaigns", response_model=list[HistoryCampaignRow])
async def get_history_campaigns(
    date_from: str = Query(..., description="ISO дата начала"),
    date_to: str = Query(..., description="ISO дата конца"),
    offer_code: str | None = Query(None),
    campaign_name: str | None = Query(None, description="Фильтр по кампании"),
    sort_by: str = Query("spend"),
    sort_dir: str = Query("desc"),
    db: AsyncSession = Depends(get_db),
) -> list[HistoryCampaignRow]:
    """Таблица кампаний за период с агрегированными метриками."""
    d_from = _parse_iso_date(date_from)
    d_to = _parse_iso_date(date_to)
    dt_from = _date_to_datetime(d_from)
    dt_to = _date_to_datetime(d_to + timedelta(days=1))

    # Загружаем кампании оффера, если указан offer_code
    offer_campaigns: set[str] | None = None
    if offer_code:
        offer_campaigns = await _load_campaigns_for_offer(db, offer_code)

    archives = await _load_archives(db, dt_from, dt_to)
    grouped = _group_campaigns_from_archives(
        archives,
        offer_campaigns,
        campaign_name,
    )

    # Добавляем live данные для «сегодня»
    today = date.today()
    if d_from <= today <= d_to:
        live = await _load_live_ads_for_today(db, offer_campaigns, campaign_name)
        for data in live.values():
            cname = data["campaign_name"]
            if not cname:
                continue
            if cname not in grouped:
                grouped[cname] = {
                    "spend": Decimal("0"),
                    "clicks": 0,
                    "leads": 0,
                    "regs": 0,
                    "deps": 0,
                }
            g = grouped[cname]
            g["spend"] += data["spend"]
            g["clicks"] += data["clicks"]
            g["leads"] += data["leads"]
            g["regs"] += data["regs"]
            g["deps"] += data["deps"]

    alerts_map, disables_map = await _count_campaign_events(
        db, dt_from, dt_to, list(grouped.keys())
    )
    fake_by_campaign = await _load_fake_deposits_by_campaign(db)

    rows = [
        _build_campaign_row(
            name,
            data,
            alerts_map.get(name, 0),
            disables_map.get(name, 0),
            fake_by_campaign=fake_by_campaign,
        )
        for name, data in grouped.items()
    ]
    return _sort_campaign_rows(rows, sort_by, sort_dir)


# ------------------------------------------------------------------
# GET /history/events
# ------------------------------------------------------------------


async def _load_alert_events(
    db: AsyncSession,
    dt_from: datetime,
    dt_to: datetime,
    offer_code: str | None,
    campaign_name: str | None = None,
) -> list[HistoryEventItem]:
    """Загружает алерт-события за период."""
    q = select(AlertEvent).where(
        and_(
            AlertEvent.created_at >= dt_from,
            AlertEvent.created_at < dt_to,
        )
    )
    if offer_code or campaign_name:
        q = q.join(AdSnapshot, AlertEvent.ad_id == AdSnapshot.ad_id)
        if offer_code:
            q = q.where(func.lower(AdSnapshot.resolved_offer_code) == offer_code.lower())
        if campaign_name:
            q = q.where(AdSnapshot.campaign_name == campaign_name)
    result = await db.execute(q)
    items = []
    for ev in result.scalars().all():
        stage_val = ev.stage.value if ev.stage else None
        items.append(
            HistoryEventItem(
                id=str(ev.id),
                event_type="alert",
                fb_ad_id=ev.fb_ad_id,
                ad_name=ev.ad_name,
                summary=ev.reason_title or f"Алерт {stage_val}",
                stage=stage_val,
                matched_rule_codes=ev.matched_rule_codes or [],
                created_at=ev.created_at.isoformat(),
            )
        )
    return items


async def _load_disable_events(
    db: AsyncSession,
    dt_from: datetime,
    dt_to: datetime,
    offer_code: str | None,
    campaign_name: str | None = None,
) -> list[HistoryEventItem]:
    """Загружает события отключения за период."""
    q = select(DisableTask).where(
        and_(
            DisableTask.created_at >= dt_from,
            DisableTask.created_at < dt_to,
        )
    )
    if offer_code or campaign_name:
        q = q.join(AdSnapshot, DisableTask.ad_id == AdSnapshot.ad_id)
        if offer_code:
            q = q.where(func.lower(AdSnapshot.resolved_offer_code) == offer_code.lower())
        if campaign_name:
            q = q.where(AdSnapshot.campaign_name == campaign_name)
    result = await db.execute(q)
    items = []
    for t in result.scalars().all():
        items.append(
            HistoryEventItem(
                id=str(t.id),
                event_type="disable",
                fb_ad_id=t.fb_ad_id,
                ad_name=t.ad_name,
                summary=f"Отключение: {t.status.value}",
                status=t.status.value,
                created_at=t.created_at.isoformat(),
            )
        )
    return items


async def _load_enable_events(
    db: AsyncSession,
    dt_from: datetime,
    dt_to: datetime,
    offer_code: str | None,
    campaign_name: str | None = None,
) -> list[HistoryEventItem]:
    """Загружает события включения за период."""
    q = select(EnableTask).where(
        and_(
            EnableTask.created_at >= dt_from,
            EnableTask.created_at < dt_to,
        )
    )
    if offer_code or campaign_name:
        q = q.join(AdSnapshot, EnableTask.ad_id == AdSnapshot.ad_id)
        if offer_code:
            q = q.where(func.lower(AdSnapshot.resolved_offer_code) == offer_code.lower())
        if campaign_name:
            q = q.where(AdSnapshot.campaign_name == campaign_name)
    result = await db.execute(q)
    items = []
    for t in result.scalars().all():
        items.append(
            HistoryEventItem(
                id=str(t.id),
                event_type="enable",
                fb_ad_id=t.fb_ad_id,
                ad_name=t.ad_name,
                summary=f"Включение: {t.status.value}",
                status=t.status.value,
                created_at=t.created_at.isoformat(),
            )
        )
    return items


@router.get("/history/events", response_model=HistoryEventsPage)
async def get_history_events(
    date_from: str = Query(..., description="ISO дата начала"),
    date_to: str = Query(..., description="ISO дата конца"),
    offer_code: str | None = Query(None),
    campaign_name: str | None = Query(None, description="Фильтр по кампании"),
    event_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> HistoryEventsPage:
    """Лента событий с пагинацией."""
    d_from = _parse_iso_date(date_from)
    d_to = _parse_iso_date(date_to)
    dt_from = _date_to_datetime(d_from)
    dt_to = _date_to_datetime(d_to + timedelta(days=1))

    all_items: list[HistoryEventItem] = []

    if not event_type or event_type == "alert":
        all_items.extend(
            await _load_alert_events(db, dt_from, dt_to, offer_code, campaign_name),
        )
    if not event_type or event_type == "disable":
        all_items.extend(
            await _load_disable_events(db, dt_from, dt_to, offer_code, campaign_name),
        )
    if not event_type or event_type == "enable":
        all_items.extend(
            await _load_enable_events(db, dt_from, dt_to, offer_code, campaign_name),
        )

    # Сортировка по дате (новые первыми)
    all_items.sort(key=lambda x: x.created_at, reverse=True)
    total = len(all_items)
    page = all_items[offset : offset + limit]

    return HistoryEventsPage(
        items=page,
        total=total,
        limit=limit,
        offset=offset,
    )


# ------------------------------------------------------------------
# GET /history/offers
# ------------------------------------------------------------------


async def _load_campaign_offer_map(
    db: AsyncSession,
) -> dict[str, str]:
    """Загружает маппинг campaign_name → resolved_offer_code из AdSnapshot."""
    q = (
        select(AdSnapshot.campaign_name, AdSnapshot.resolved_offer_code)
        .where(AdSnapshot.resolved_offer_code.isnot(None))
        .distinct()
    )
    result = await db.execute(q)
    return {row[0]: row[1] for row in result.all()}


def _group_by_offer_code(
    archives: list[CabinetDayArchive],
    campaign_offer_map: dict[str, str],
) -> dict[str, dict]:
    """Группирует метрики по offer code.

    Приоритет: ads_json (offer_code per-ad), fallback на campaigns_json + campaign mapping.
    """
    grouped: dict[str, dict] = {}
    for arch in archives:
        # Предпочитаем ads_json — там offer_code хранится напрямую
        if arch.ads_json:
            for ad in arch.ads_json:
                code = ad.get("offer_code")
                if not code:
                    continue
                if code not in grouped:
                    grouped[code] = {
                        "spend": Decimal("0"),
                        "clicks": 0,
                        "regs": 0,
                        "deps": 0,
                    }
                g = grouped[code]
                g["spend"] += Decimal(str(ad.get("spend", 0)))
                g["clicks"] += int(ad.get("clicks", 0))
                g["regs"] += int(ad.get("registrations", 0))
                g["deps"] += int(ad.get("deposits", 0))
        else:
            # Fallback для старых архивов без ads_json
            for c in arch.campaigns_json or []:
                campaign = c.get("campaign") or ""
                code = campaign_offer_map.get(campaign)
                if not code:
                    continue
                if code not in grouped:
                    grouped[code] = {
                        "spend": Decimal("0"),
                        "clicks": 0,
                        "regs": 0,
                        "deps": 0,
                    }
                g = grouped[code]
                g["spend"] += Decimal(str(c.get("spend", 0)))
                g["clicks"] += int(c.get("clicks", 0))
                g["regs"] += int(c.get("registrations", 0))
                g["deps"] += int(c.get("deposits", 0))
    return grouped


async def _load_offers_map(
    db: AsyncSession,
    offer_codes: list[str],
) -> dict[str, Offer]:
    """Загружает офферы по кодам и возвращает словарь code→Offer."""
    if not offer_codes:
        return {}
    q = select(Offer).where(func.upper(Offer.code).in_([c.upper() for c in offer_codes]))
    result = await db.execute(q)
    return {o.code.upper(): o for o in result.scalars().all()}


async def _count_offer_events(
    db: AsyncSession,
    dt_from: datetime,
    dt_to: datetime,
    offer_codes: list[str],
) -> tuple[dict[str, int], dict[str, int]]:
    """Считает алерты и отключения по offer_code.

    Returns:
        (alerts_by_offer, disables_by_offer)
    """
    if not offer_codes:
        return {}, {}

    upper_codes = [c.upper() for c in offer_codes]

    # Алерты по офферам
    alerts_q = (
        select(
            func.upper(AdSnapshot.resolved_offer_code),
            func.count(),
        )
        .join(AlertEvent, AlertEvent.ad_id == AdSnapshot.ad_id)
        .where(
            and_(
                AlertEvent.created_at >= dt_from,
                AlertEvent.created_at < dt_to,
                func.upper(AdSnapshot.resolved_offer_code).in_(upper_codes),
            )
        )
        .group_by(func.upper(AdSnapshot.resolved_offer_code))
    )
    alerts_rows = (await db.execute(alerts_q)).all()
    alerts_map = {code: cnt for code, cnt in alerts_rows}

    # Отключения по офферам
    disables_q = (
        select(
            func.upper(AdSnapshot.resolved_offer_code),
            func.count(),
        )
        .join(DisableTask, DisableTask.ad_id == AdSnapshot.ad_id)
        .where(
            and_(
                DisableTask.created_at >= dt_from,
                DisableTask.created_at < dt_to,
                DisableTask.status == DisableTaskStatus.SUCCEEDED,
                func.upper(AdSnapshot.resolved_offer_code).in_(upper_codes),
            )
        )
        .group_by(func.upper(AdSnapshot.resolved_offer_code))
    )
    disables_rows = (await db.execute(disables_q)).all()
    disables_map = {code: cnt for code, cnt in disables_rows}

    return alerts_map, disables_map


def _calc_profit(
    revenue: Decimal,
    spend: Decimal,
    clicks: int,
    install_cost: Decimal,
    agent_commission_pct: Decimal,
) -> Decimal:
    """Считает profit с учётом комиссий."""
    install_costs = Decimal(str(clicks)) * install_cost
    agent_fee = revenue * agent_commission_pct / Decimal("100")
    return revenue - spend - install_costs - agent_fee


def _build_offer_summary(
    code: str,
    metrics: dict,
    offer: Offer | None,
    alerts_count: int,
    disables_count: int,
    install_cost: Decimal = Decimal("0"),
    agent_commission_pct: Decimal = Decimal("0"),
    fake_by_offer: dict[str, int] | None = None,
) -> HistoryOfferSummary:
    """Строит HistoryOfferSummary для одного оффера."""
    spend = metrics["spend"]
    regs = metrics["regs"]
    raw_deps = metrics["deps"]
    fake_count = (fake_by_offer or {}).get(code.upper(), 0)
    deps = max(0, raw_deps - fake_count)
    clicks = metrics.get("clicks", 0)
    cpa = offer.cpa_amount if offer else Decimal("0")
    revenue = Decimal(str(deps)) * cpa

    profit = None
    if cpa > 0:
        profit = _calc_profit(revenue, spend, clicks, install_cost, agent_commission_pct)

    return HistoryOfferSummary(
        offer_code=code,
        offer_name=offer.name if offer else code,
        total_spend=spend,
        total_deposits=deps,
        total_registrations=regs,
        avg_cpr=_safe_div(spend, regs),
        avg_spend_per_dep=_safe_div(spend, deps),
        roas=(revenue / spend).quantize(Decimal("0.01")) if spend > 0 else None,
        profit=profit,
        alerts_count=alerts_count,
        disables_count=disables_count,
    )


@router.get("/history/offers", response_model=list[HistoryOfferSummary])
async def get_history_offers(
    date_from: str = Query(..., description="ISO дата начала"),
    date_to: str = Query(..., description="ISO дата конца"),
    db: AsyncSession = Depends(get_db),
) -> list[HistoryOfferSummary]:
    """Сводка по офферам за период."""
    d_from = _parse_iso_date(date_from)
    d_to = _parse_iso_date(date_to)
    dt_from = _date_to_datetime(d_from)
    dt_to = _date_to_datetime(d_to + timedelta(days=1))

    archives = await _load_archives(db, dt_from, dt_to)
    campaign_offer_map = await _load_campaign_offer_map(db)
    grouped = _group_by_offer_code(archives, campaign_offer_map)

    # Добавляем live данные для «сегодня»
    today = date.today()
    if d_from <= today <= d_to:
        live = await _load_live_ads_for_today(db, None, None)
        for data in live.values():
            code = data.get("offer_code")
            if not code:
                continue
            if code not in grouped:
                grouped[code] = {
                    "spend": Decimal("0"),
                    "clicks": 0,
                    "regs": 0,
                    "deps": 0,
                }
            g = grouped[code]
            g["spend"] += data["spend"]
            g["clicks"] += data["clicks"]
            g["regs"] += data["regs"]
            g["deps"] += data["deps"]

    if not grouped:
        return []

    # Загружаем глобальные настройки комиссий
    settings = await _load_observer_settings(db)
    install_cost = settings.install_cost if settings else Decimal("0.02")
    agent_pct = settings.agent_commission_percent if settings else Decimal("3")

    codes = list(grouped.keys())
    offers_map = await _load_offers_map(db, codes)
    alerts_map, disables_map = await _count_offer_events(db, dt_from, dt_to, codes)
    fake_by_offer = await _load_fake_deposits_by_offer(db)

    rows = [
        _build_offer_summary(
            code,
            metrics,
            offers_map.get(code.upper()),
            alerts_map.get(code.upper(), 0),
            disables_map.get(code.upper(), 0),
            install_cost=install_cost,
            agent_commission_pct=agent_pct,
            fake_by_offer=fake_by_offer,
        )
        for code, metrics in grouped.items()
    ]
    # Сортировка по spend DESC
    rows.sort(key=lambda r: r.total_spend, reverse=True)
    return rows


# ------------------------------------------------------------------
# GET /history/ads — per-ad данные за период
# ------------------------------------------------------------------

_AD_SORT_KEYS = {
    "spend": "total_spend",
    "clicks": "total_clicks",
    "leads": "total_leads",
    "registrations": "total_registrations",
    "deposits": "total_deposits",
    "ad_name": "ad_name",
    "campaign_name": "campaign_name",
}


def _group_ads_from_archives(
    archives: list[CabinetDayArchive],
    offer_campaigns: set[str] | None,
    campaign_name: str | None = None,
) -> dict[str, dict]:
    """Группирует per-ad данные из архивов по fb_ad_id."""
    grouped: dict[str, dict] = {}
    for arch in archives:
        for ad in arch.ads_json or []:
            ad_campaign = ad.get("campaign_name", "")
            # Фильтрация по кампании/офферу
            if offer_campaigns is not None and ad_campaign not in offer_campaigns:
                continue
            if campaign_name and ad_campaign != campaign_name:
                continue
            fb_ad_id = ad.get("fb_ad_id", "")
            if not fb_ad_id:
                continue
            if fb_ad_id not in grouped:
                grouped[fb_ad_id] = {
                    "fb_ad_id": fb_ad_id,
                    "ad_name": ad.get("ad_name", ""),
                    "campaign_name": ad_campaign,
                    "offer_code": ad.get("offer_code"),
                    "spend": Decimal("0"),
                    "clicks": 0,
                    "leads": 0,
                    "regs": 0,
                    "deps": 0,
                }
            g = grouped[fb_ad_id]
            g["spend"] += Decimal(str(ad.get("spend", 0)))
            g["clicks"] += int(ad.get("clicks", 0))
            g["leads"] += int(ad.get("leads", 0))
            g["regs"] += int(ad.get("registrations", 0))
            g["deps"] += int(ad.get("deposits", 0))
            # Обновляем имя/кампанию/оффер из последнего архива
            if ad.get("ad_name"):
                g["ad_name"] = ad["ad_name"]
            if ad.get("offer_code"):
                g["offer_code"] = ad["offer_code"]
    return grouped


async def _load_live_ads_for_today(
    db: AsyncSession,
    offer_campaigns: set[str] | None,
    campaign_name: str | None = None,
) -> dict[str, dict]:
    """Загружает live AdSnapshot данные для текущего дня."""
    q = select(AdSnapshot)
    if campaign_name:
        q = q.where(AdSnapshot.campaign_name == campaign_name)
    result = await db.execute(q)
    snapshots = result.scalars().all()

    grouped: dict[str, dict] = {}
    for s in snapshots:
        if offer_campaigns is not None and s.campaign_name not in offer_campaigns:
            continue
        grouped[s.fb_ad_id] = {
            "fb_ad_id": s.fb_ad_id,
            "ad_name": s.ad_name,
            "campaign_name": s.campaign_name,
            "offer_code": s.resolved_offer_code,
            "spend": Decimal(str(s.spend or 0)),
            "clicks": int(s.clicks or 0),
            "leads": int(s.leads or 0),
            "regs": int(s.registrations or 0),
            "deps": int(s.deposits or 0),
        }
    return grouped


def _build_history_ad_row(data: dict) -> HistoryAdRow:
    """Строит HistoryAdRow из агрегированных данных."""
    spend = data["spend"]
    clicks = data["clicks"]
    leads = data["leads"]
    regs = data["regs"]
    deps = data["deps"]
    return HistoryAdRow(
        fb_ad_id=data["fb_ad_id"],
        ad_name=data["ad_name"],
        campaign_name=data["campaign_name"],
        offer_code=data.get("offer_code"),
        total_spend=spend,
        total_clicks=clicks,
        total_leads=leads,
        total_registrations=regs,
        total_deposits=deps,
        avg_cpc=_safe_div(spend, clicks),
        avg_cpl=_safe_div(spend, leads),
        avg_cpr=_safe_div(spend, regs),
        avg_spend_per_dep=_safe_div(spend, deps),
    )


@router.get("/history/ads", response_model=list[HistoryAdRow])
async def get_history_ads(
    date_from: str = Query(..., description="ISO дата начала"),
    date_to: str = Query(..., description="ISO дата конца"),
    offer_code: str | None = Query(None),
    campaign_name: str | None = Query(None),
    sort_by: str = Query("spend"),
    sort_dir: str = Query("desc"),
    db: AsyncSession = Depends(get_db),
) -> list[HistoryAdRow]:
    """Per-ad метрики за период (архив + live для сегодня)."""
    d_from = _parse_iso_date(date_from)
    d_to = _parse_iso_date(date_to)
    dt_from = _date_to_datetime(d_from)
    dt_to = _date_to_datetime(d_to + timedelta(days=1))

    offer_campaigns: set[str] | None = None
    if offer_code:
        offer_campaigns = await _load_campaigns_for_offer(db, offer_code)

    # Данные из архивов
    archives = await _load_archives(db, dt_from, dt_to)
    grouped = _group_ads_from_archives(archives, offer_campaigns, campaign_name)

    # Добавляем live данные для «сегодня» если период включает текущий день
    today = date.today()
    if d_from <= today <= d_to:
        live = await _load_live_ads_for_today(db, offer_campaigns, campaign_name)
        for fb_ad_id, data in live.items():
            if fb_ad_id in grouped:
                # Суммируем к архивным данным
                g = grouped[fb_ad_id]
                g["spend"] += data["spend"]
                g["clicks"] += data["clicks"]
                g["leads"] += data["leads"]
                g["regs"] += data["regs"]
                g["deps"] += data["deps"]
                # Обновляем имя из live (актуальнее)
                g["ad_name"] = data["ad_name"]
                g["campaign_name"] = data["campaign_name"]
                g["offer_code"] = data.get("offer_code") or g.get("offer_code")
            else:
                grouped[fb_ad_id] = data

    rows = [_build_history_ad_row(data) for data in grouped.values()]

    # Сортировка
    attr = _AD_SORT_KEYS.get(sort_by, "total_spend")
    reverse = sort_dir.lower() == "desc"
    rows.sort(key=lambda r: getattr(r, attr, 0) or 0, reverse=reverse)

    return rows

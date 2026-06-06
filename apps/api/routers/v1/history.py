# -*- coding: utf-8 -*-
"""Роутер HistoryPage — 6 endpoints агрегации за произвольный период.

Endpoints (prefix /api от auto-discovery):
    GET /history/summary    — сводка: spend/impressions/clicks/leads/deps + алерты + задачи
    GET /history/timeline   — UNION ALL алертов и задач, сорт по timestamp DESC
    GET /history/campaigns  — GROUP BY кампании, сорт по spend DESC
    GET /history/events     — только AlertEvent с drill-down по campaign_id/stage
    GET /history/offers     — GROUP BY офферу через JOIN fb_campaigns.offer_id
    GET /history/ads        — GROUP BY объявлению + last_alert + last_disable

Тонкий слой: валидация окна + маппинг строк в Pydantic-схемы. Весь SQL вынесен
в core/dashboard/history_queries.py (был 743-строчный god-router).

Все endpoint'ы:
- Принимают from_iso/to_iso (ISO-8601), default — последние 30 дней.
- Диапазон не может превышать 90 дней (иначе 422).
- Партиционированные таблицы фильтруются по partition-key (см. history_queries).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query

from apps.api.deps import DepEngine
from apps.api.routers.v1.schemas.history import (
    HistoryAdOut,
    HistoryAlerts,
    HistoryCampaignOut,
    HistoryEventOut,
    HistoryOfferOut,
    HistoryRuleCount,
    HistorySummaryOut,
    HistoryTasks,
    HistoryTimelineItem,
    HistoryTotals,
)
from apps.api.utils.partition import default_window
from apps.api.utils.status_mapper import to_frontend_task_status
from core.dashboard import history_queries as hq

logger = logging.getLogger(__name__)

router = APIRouter(tags=["history"])

# Жёсткие лимиты
_MAX_RANGE_DAYS = 90
_DEFAULT_RANGE_HOURS = 720  # 30 дней
_TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")


def _parse_window(
    from_iso: str | None,
    to_iso: str | None,
) -> tuple[datetime, datetime]:
    """Парсит временное окно из параметров запроса.

    Defaults: from = now - 30 дней, to = now.
    Валидация: to >= from, range <= 90 дней.
    """
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

    # Нормализуем aware-UTC
    if from_dt.tzinfo is None:
        from_dt = from_dt.replace(tzinfo=UTC)
    if to_dt.tzinfo is None:
        to_dt = to_dt.replace(tzinfo=UTC)

    if to_dt < from_dt:
        raise HTTPException(status_code=422, detail="to_iso должен быть >= from_iso")

    if (to_dt - from_dt) > timedelta(days=_MAX_RANGE_DAYS):
        raise HTTPException(
            status_code=422,
            detail=f"Диапазон не может превышать {_MAX_RANGE_DAYS} дней",
        )

    return from_dt, to_dt


def _fmt_decimal(value: Decimal | None) -> str:
    """Форматирует Decimal в строку с 2 знаками. None → '0.00'."""
    if value is None:
        return "0.00"
    return f"{value:.2f}"


def _safe_cpl(spend: Decimal | None, leads: int) -> str | None:
    """Вычисляет cost_per_lead = spend / leads. Возвращает None если leads == 0."""
    if not leads or spend is None:
        return None
    return _fmt_decimal(spend / leads)


# ─────────────────────── GET /history/summary ────────────────────────────────


@router.get("/history/summary", response_model=HistorySummaryOut)
async def get_history_summary(
    engine: DepEngine,
    from_iso: str | None = Query(default=None, description="ISO-8601 начало периода"),
    to_iso: str | None = Query(default=None, description="ISO-8601 конец периода"),
) -> HistorySummaryOut:
    """Сводная агрегация за период: spend/метрики + алерты + задачи.

    Партиционированные таблицы фильтруются по partition-key. 5 запросов на одном
    соединении (см. core.dashboard.history_queries).
    """
    from_dt, to_dt = _parse_window(from_iso, to_iso)

    async with engine.connect() as conn:
        m_row = await hq.fetch_summary_metrics(conn, from_dt, to_dt)
        active_row = await hq.fetch_active_ads_count(conn)
        alert_rows = await hq.fetch_alerts_by_stage(conn, from_dt, to_dt)
        rule_rows = await hq.fetch_rules_breakdown(conn, from_dt, to_dt)
        t_row = await hq.fetch_tasks_summary(conn, from_dt, to_dt)

    warning_count = 0
    stop_count = 0
    for r in alert_rows:
        if r.stage == "warning":
            warning_count = r.cnt
        elif r.stage == "stop":
            stop_count = r.cnt

    by_rule = [HistoryRuleCount(rule_code=r.rule_code, count=r.cnt) for r in rule_rows]

    return HistorySummaryOut(
        from_iso=from_dt,
        to_iso=to_dt,
        totals=HistoryTotals(
            spend=_fmt_decimal(m_row.spend),
            impressions=int(m_row.impressions),
            clicks=int(m_row.clicks),
            leads=int(m_row.leads),
            registrations=int(m_row.registrations),
            deposits=int(m_row.deposits),
            active_ads_count=int(active_row.cnt),
        ),
        alerts=HistoryAlerts(
            warning_count=int(warning_count),
            stop_count=int(stop_count),
            by_rule=by_rule,
        ),
        tasks=HistoryTasks(
            disable_completed=int(t_row.disable_completed),
            disable_failed=int(t_row.disable_failed),
            enable_completed=int(t_row.enable_completed),
        ),
    )


# ─────────────────────── GET /history/timeline ───────────────────────────────


@router.get("/history/timeline", response_model=list[HistoryTimelineItem])
async def get_history_timeline(
    engine: DepEngine,
    from_iso: str | None = Query(default=None),
    to_iso: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[HistoryTimelineItem]:
    """Объединённая лента: AlertEvent + terminal TaskQueue (succeeded/failed/cancelled).

    UNION ALL с JOIN FbAd→FbAdset→FbCampaign для имён. Сорт по ts DESC.
    Только terminal задачи (succeeded/failed/cancelled) — pending/running не включаются.
    """
    from_dt, to_dt = _parse_window(from_iso, to_iso)

    rows = await hq.fetch_timeline(engine, from_dt, to_dt, limit)

    result: list[HistoryTimelineItem] = []
    for r in rows:
        rule_codes: list[str] | None = None
        if r.rule_codes_raw:
            try:
                rule_codes = json.loads(r.rule_codes_raw)
            except Exception:
                rule_codes = []

        task_status_fe: str | None = None
        if r.task_status:
            try:
                task_status_fe = to_frontend_task_status(r.task_status)
            except ValueError:
                task_status_fe = r.task_status.upper()

        result.append(
            HistoryTimelineItem(
                event_type=r.event_type,
                ts=r.ts,
                fb_ad_id=r.fb_ad_id,
                ad_name=r.ad_name,
                campaign_name=r.campaign_name,
                stage=r.stage,
                rule_codes=rule_codes,
                task_type=r.task_type,
                task_status=task_status_fe,
            )
        )
    return result


# ─────────────────────── GET /history/campaigns ──────────────────────────────


@router.get("/history/campaigns", response_model=list[HistoryCampaignOut])
async def get_history_campaigns(
    engine: DepEngine,
    from_iso: str | None = Query(default=None),
    to_iso: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[HistoryCampaignOut]:
    """GROUP BY кампании: spend/leads/deps + active_ads_count + alerts_count.

    Партиционированная ad_metrics фильтруется по cycle_ts.
    active_ads_count — через last_seen_at >= NOW() - 7d.
    """
    from_dt, to_dt = _parse_window(from_iso, to_iso)

    rows = await hq.fetch_campaigns(engine, from_dt, to_dt, limit)

    result = []
    for r in rows:
        spend = r.spend
        leads = r.leads
        result.append(
            HistoryCampaignOut(
                campaign_id=str(r.campaign_id),
                fb_campaign_id=r.fb_campaign_id,
                campaign_name=r.campaign_name,
                offer_code=r.offer_code,
                spend=_fmt_decimal(spend),
                leads=leads,
                registrations=r.registrations,
                deposits=r.deposits,
                active_ads_count=r.active_ads_count,
                alerts_count=r.alerts_count,
                cost_per_lead=_safe_cpl(spend, leads),
            )
        )
    return result


# ─────────────────────── GET /history/events ─────────────────────────────────


@router.get("/history/events", response_model=list[HistoryEventOut])
async def get_history_events(
    engine: DepEngine,
    from_iso: str | None = Query(default=None),
    to_iso: str | None = Query(default=None),
    campaign_id: str | None = Query(default=None, description="UUID кампании для drill-down"),
    fb_ad_id: str | None = Query(default=None, description="fb_ad_id конкретного объявления"),
    stage: str | None = Query(default=None, description="warning | stop"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[HistoryEventOut]:
    """AlertEvent drill-down с JOIN ad/adset/campaign/offer.

    Партиционированная alert_events фильтруется по created_at.
    """
    from_dt, to_dt = _parse_window(from_iso, to_iso)

    # Валидация stage
    if stage and stage not in ("warning", "stop"):
        raise HTTPException(status_code=422, detail="stage должен быть 'warning' или 'stop'")

    # Конвертируем campaign_id в UUID-объект если передан (asyncpg не понимает ::uuid cast в text())
    campaign_uuid: uuid.UUID | None = None
    if campaign_id:
        try:
            campaign_uuid = uuid.UUID(campaign_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Неверный campaign_id: {exc}") from exc

    rows = await hq.fetch_events(
        engine,
        from_dt,
        to_dt,
        campaign_uuid=campaign_uuid,
        fb_ad_id=fb_ad_id,
        stage=stage,
        limit=limit,
    )

    return [
        HistoryEventOut(
            id=str(r.id),
            fb_ad_id=r.fb_ad_id,
            ad_name=r.ad_name,
            campaign_name=r.campaign_name,
            offer_code=r.offer_code,
            stage=r.stage,
            matched_rule_codes=r.matched_rule_codes or [],
            created_at=r.created_at,
            alert_payload=r.alert_payload,
        )
        for r in rows
    ]


# ─────────────────────── GET /history/offers ─────────────────────────────────


@router.get("/history/offers", response_model=list[HistoryOfferOut])
async def get_history_offers(
    engine: DepEngine,
    from_iso: str | None = Query(default=None),
    to_iso: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[HistoryOfferOut]:
    """GROUP BY Offer через JOIN fb_campaigns.offer_id.

    Партиционированные таблицы фильтруются по partition-key.
    """
    from_dt, to_dt = _parse_window(from_iso, to_iso)

    rows = await hq.fetch_offers(engine, from_dt, to_dt, limit)

    result = []
    for r in rows:
        spend = r.spend
        leads = r.leads
        result.append(
            HistoryOfferOut(
                offer_id=str(r.offer_id),
                offer_code=r.offer_code,
                offer_name=r.offer_name,
                spend=_fmt_decimal(spend),
                leads=leads,
                registrations=r.registrations,
                deposits=r.deposits,
                alerts_count=r.alerts_count,
                active_ads_count=r.active_ads_count,
                cost_per_lead=_safe_cpl(spend, leads),
            )
        )
    return result


# ─────────────────────── GET /history/ads ────────────────────────────────────


@router.get("/history/ads", response_model=list[HistoryAdOut])
async def get_history_ads(
    engine: DepEngine,
    from_iso: str | None = Query(default=None),
    to_iso: str | None = Query(default=None),
    campaign_id: str | None = Query(default=None, description="UUID кампании (фильтр)"),
    offer_id: str | None = Query(default=None, description="UUID оффера (фильтр)"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[HistoryAdOut]:
    """GROUP BY объявлению: spend/leads/deps + last_alert + last_disable.

    last_alert_at / last_alert_stage — через LATERAL subquery.
    last_disable_at — через LATERAL subquery по task_queue.
    Партиционированные таблицы фильтруются по partition-key.
    """
    from_dt, to_dt = _parse_window(from_iso, to_iso)

    # Конвертируем campaign_id / offer_id в UUID-объекты (asyncpg не понимает ::uuid cast в text())
    campaign_uuid: uuid.UUID | None = None
    offer_uuid: uuid.UUID | None = None
    if campaign_id:
        try:
            campaign_uuid = uuid.UUID(campaign_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Неверный campaign_id: {exc}") from exc
    if offer_id:
        try:
            offer_uuid = uuid.UUID(offer_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Неверный offer_id: {exc}") from exc

    rows = await hq.fetch_ads(
        engine,
        from_dt,
        to_dt,
        campaign_uuid=campaign_uuid,
        offer_uuid=offer_uuid,
        limit=limit,
    )

    return [
        HistoryAdOut(
            fb_ad_id=r.fb_ad_id,
            internal_id=str(r.internal_id),
            ad_name=r.ad_name,
            campaign_name=r.campaign_name,
            offer_code=r.offer_code,
            is_active=r.is_active,
            spend=_fmt_decimal(r.spend),
            leads=r.leads,
            deposits=r.deposits,
            last_alert_at=r.last_alert_at,
            last_alert_stage=r.last_alert_stage,
            last_disable_at=r.last_disable_at,
            alerts_count_in_window=r.alerts_count_in_window,
        )
        for r in rows
    ]

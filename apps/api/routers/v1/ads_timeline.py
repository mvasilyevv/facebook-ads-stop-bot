# -*- coding: utf-8 -*-
"""Роутер ads_timeline: история метрик, алертов и задач по объявлению.

Endpoints:
    GET /ads/{fb_ad_id}/timeline — timeline объявления с метриками, алертами и задачами
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select, text

from apps.api.deps import DepEngine
from apps.api.routers.v1.schemas.ads_timeline import (
    AdTimelineResponse,
    AlertRow,
    MetricRow,
    TaskRow,
)
from apps.api.utils.partition import default_window
from apps.api.utils.status_mapper import to_frontend_task_status
from core.models.catalog.fb_ad import FbAd
from core.models.catalog.fb_adset import FbAdset
from core.models.catalog.fb_campaign import FbCampaign
from core.models.observer.ad_metrics import AdMetrics
from core.models.observer.alert_event import AlertEvent
from core.models.tasks.task_queue import TaskQueue

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ads"])


@router.get("/ads/{fb_ad_id}/timeline", response_model=AdTimelineResponse)
async def get_ad_timeline(
    fb_ad_id: str,
    engine: DepEngine,
    from_iso: str | None = Query(default=None, description="ISO-8601 начало окна"),
    to_iso: str | None = Query(default=None, description="ISO-8601 конец окна"),
    include_metrics: bool = Query(default=True, description="Включить метрики"),
    include_alerts: bool = Query(default=True, description="Включить алерты"),
    include_tasks: bool = Query(default=True, description="Включить задачи"),
) -> AdTimelineResponse:
    """Возвращает timeline объявления: метрики, алерты FSM и задачи из outbox.

    Временное окно по умолчанию — последние 7 дней.
    Партиционированные таблицы (ad_metrics, alert_events) фильтруются обязательно по времени.
    """
    # Парсим временное окно
    if from_iso or to_iso:
        try:
            from_dt = datetime.fromisoformat(from_iso) if from_iso else default_window()[0]
            to_dt = datetime.fromisoformat(to_iso) if to_iso else datetime.now(UTC)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=f"Неверный формат даты: {exc}") from exc
    else:
        from_dt, to_dt = default_window()

    async with engine.connect() as conn:
        # 1. Ресолв fb_ad_id → FbAd + JOIN adset + campaign для имён
        ad_stmt = (
            select(
                FbAd.id,
                FbAd.fb_ad_id,
                FbAd.ad_name,
                FbAdset.adset_name,
                FbCampaign.campaign_name,
            )
            .join(FbAdset, FbAd.adset_id == FbAdset.id)
            .join(FbCampaign, FbAdset.campaign_id == FbCampaign.id)
            .where(FbAd.fb_ad_id == fb_ad_id)
        )
        ad_row = (await conn.execute(ad_stmt)).one_or_none()
        if ad_row is None:
            raise HTTPException(status_code=404, detail=f"Объявление {fb_ad_id!r} не найдено")

        internal_id = ad_row.id
        ad_name = ad_row.ad_name
        campaign_name = ad_row.campaign_name
        adset_name = ad_row.adset_name

        # 2. Метрики (partitioned by cycle_ts — обязателен фильтр по cycle_ts)
        metrics: list[MetricRow] = []
        if include_metrics:
            metrics_stmt = (
                select(AdMetrics)
                .where(AdMetrics.ad_id == internal_id)
                .where(AdMetrics.cycle_ts >= from_dt)
                .where(AdMetrics.cycle_ts <= to_dt)
                .order_by(AdMetrics.cycle_ts.asc())
            )
            metrics_rows = (await conn.execute(metrics_stmt)).fetchall()
            metrics = [
                MetricRow(
                    cycle_ts=r.cycle_ts,
                    spend=r.spend,
                    impressions=r.impressions,
                    clicks=r.clicks,
                    leads=r.leads,
                    deposits=r.deposits,
                )
                for r in metrics_rows
            ]

        # 3. Алерты (partitioned by created_at — обязателен фильтр по created_at)
        alerts: list[AlertRow] = []
        if include_alerts:
            alerts_stmt = (
                select(AlertEvent)
                .where(AlertEvent.ad_id == internal_id)
                .where(AlertEvent.created_at >= from_dt)
                .where(AlertEvent.created_at <= to_dt)
                .order_by(AlertEvent.created_at.asc())
            )
            alerts_rows = (await conn.execute(alerts_stmt)).fetchall()
            alerts = [
                AlertRow(
                    id=r.id,
                    stage=r.stage,
                    matched_rule_codes=r.matched_rule_codes or [],
                    # triggered_by_rule_codes = matched_rule_codes (одно поле в v2)
                    triggered_by_rule_codes=r.matched_rule_codes or [],
                    created_at=r.created_at,
                )
                for r in alerts_rows
            ]

        # 4. Задачи из task_queue (GIN-индекс по payload)
        tasks: list[TaskRow] = []
        if include_tasks:
            tasks_stmt = (
                select(TaskQueue)
                .where(text("payload->>'fb_ad_id' = :fb_ad_id").bindparams(fb_ad_id=fb_ad_id))
                .where(TaskQueue.created_at >= from_dt)
                .where(TaskQueue.created_at <= to_dt)
                .order_by(TaskQueue.created_at.asc())
            )
            tasks_rows = (await conn.execute(tasks_stmt)).fetchall()
            tasks = [
                TaskRow(
                    id=r.id,
                    task_type=r.task_type,
                    status=to_frontend_task_status(r.status),
                    requested_by=r.requested_by,
                    created_at=r.created_at,
                    completed_at=r.completed_at,
                    error_message=r.last_error,
                )
                for r in tasks_rows
            ]

    return AdTimelineResponse(
        fb_ad_id=fb_ad_id,
        internal_id=internal_id,
        ad_name=ad_name,
        campaign_name=campaign_name,
        adset_name=adset_name,
        offer_code=None,  # отсутствует в v2 ORM напрямую
        from_iso=from_dt,
        to_iso=to_dt,
        metrics=metrics,
        alerts=alerts,
        tasks=tasks,
    )

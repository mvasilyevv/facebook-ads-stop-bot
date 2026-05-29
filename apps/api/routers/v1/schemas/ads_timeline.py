# -*- coding: utf-8 -*-
"""Pydantic-схемы для роутера ads_timeline (GET /ads/{fb_ad_id}/timeline)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class MetricRow(BaseModel):
    """Одна строка метрик из ad_metrics (partitioned by cycle_ts)."""

    model_config = ConfigDict(from_attributes=True)

    cycle_ts: datetime
    spend: Decimal | None = None
    impressions: int | None = None
    clicks: int | None = None
    leads: int | None = None
    deposits: int | None = None
    # delivery_status отсутствует в ORM — возвращаем null для совместимости с фронтом
    delivery_status: None = None


class AlertRow(BaseModel):
    """Одна строка alert_events (partitioned by created_at)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    stage: str
    matched_rule_codes: list[str]
    # triggered_by_rule_codes = matched_rule_codes (одно и то же в текущей схеме)
    triggered_by_rule_codes: list[str]
    created_at: datetime


class TaskRow(BaseModel):
    """Одна строка task_queue, относящаяся к данному объявлению."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    task_type: str
    # status маппится в UPPERCASE через to_frontend_task_status
    status: str
    requested_by: str
    created_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None


class AdTimelineResponse(BaseModel):
    """Ответ GET /ads/{fb_ad_id}/timeline."""

    model_config = ConfigDict(from_attributes=True)

    fb_ad_id: str
    internal_id: uuid.UUID
    ad_name: str
    campaign_name: str | None = None
    adset_name: str | None = None
    # offer_code: нет прямого поля в ORM, будет null
    offer_code: str | None = None
    from_iso: datetime
    to_iso: datetime
    metrics: list[MetricRow]
    alerts: list[AlertRow]
    tasks: list[TaskRow]

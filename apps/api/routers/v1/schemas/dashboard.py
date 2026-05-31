# -*- coding: utf-8 -*-
"""Pydantic-схемы для dashboard endpoint'ов.

Схемы — для строгой типизации в OpenAPI и компиляции v1-роутера.
В runtime сам endpoint часто возвращает `list[dict]` (через
`response_model=list[Schema]`), а схема используется как контракт shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MetricsBlock(BaseModel):
    """Блок метрик (последняя строка ad_metrics за окно 7 дней).

    Decimal'ы сериализуются как str — стабильный путь без потери точности.
    """

    model_config = ConfigDict(from_attributes=False)

    cycle_ts: str
    spend: str | None = None
    impressions: int | None = None
    clicks: int | None = None
    ctr: str | None = None
    cpc: str | None = None
    cpm: str | None = None
    reach: int | None = None
    frequency: str | None = None
    leads: int | None = None
    cost_per_lead: str | None = None
    registrations: int | None = None
    cost_per_registration: str | None = None
    deposits: int | None = None


class AdSnapshotOut(BaseModel):
    """Композитный snapshot одного ad'а для /dashboard/ads и /dashboard/incidents."""

    model_config = ConfigDict(from_attributes=False)

    fb_ad_id: str
    internal_id: str
    ad_name: str
    campaign_name: str | None = None
    adset_name: str | None = None
    offer_code: str | None = None
    offer_id: str | None = None

    alert_state: str = "normal"
    snoozed_until: str | None = None
    open_state_token: str | None = None
    last_warning_at: str | None = None
    last_stop_at: str | None = None

    is_active: bool
    last_seen_at: str | None = None

    delivery_status: str | None = None
    meta_ad_status: str | None = None

    # Коды сработавших правил из последнего AlertEvent (по стадии: stop/warning).
    # Заполняются в core/dashboard/snapshot.py::_build_row_dict из LATERAL last_ev;
    # default [] — если для ad'а ещё нет AlertEvent.
    stop_rule_codes: list[str] = Field(default_factory=list)
    warning_rule_codes: list[str] = Field(default_factory=list)

    metrics: MetricsBlock | None = None


class IncidentOut(AdSnapshotOut):
    """AdSnapshotOut + incident-специфичные поля для /dashboard/incidents."""

    incident_open_since: str | None = None
    incident_duration_seconds: int | None = None
    transitions_count: int = 0


class AlertEventOut(BaseModel):
    """Одна запись alert_events с JOIN'ом по fb_ads/offers."""

    model_config = ConfigDict(from_attributes=False)

    id: str
    fb_ad_id: str | None = None
    ad_name: str | None = None
    campaign_name: str | None = None
    offer_code: str | None = None

    stage: str  # warning | stop
    matched_rule_codes: list[str] = Field(default_factory=list)
    # triggered_by_rule_codes — alias matched_rule_codes (BL-12-mig): отдельного
    # поля в ORM нет, дублируем сработавшие правила. Тип nullable сохранён для
    # обратной совместимости контракта.
    triggered_by_rule_codes: list[str] | None = None

    created_at: datetime
    alert_payload: dict[str, Any] | None = None

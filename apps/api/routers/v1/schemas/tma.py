# -*- coding: utf-8 -*-
"""Pydantic-схемы для TMA action-endpoint'ов (BL-15 Этап 2).

Все эти endpoint'ы — под Bearer-guard (DepTmaPrincipal). Money-действия
(disable) меняют реальные объявления, поэтому контракт строгий.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TmaAdMetrics(BaseModel):
    """Метрики для AdDetailPage (последняя строка ad_metrics). Decimal → str."""

    spend: str | None = None
    leads: int | None = None
    deposits: int | None = None
    cpc: str | None = None
    ctr: str | None = None
    registrations: int | None = None
    cost_per_lead: str | None = None


class TmaRecentAlert(BaseModel):
    """Одна запись истории алертов (alert_events) для AdDetailPage."""

    stage: str  # WARNING | STOP (uppercase для фронта)
    created_at: str | None = None
    reason_title: str | None = None


class TmaAdDetailResponse(BaseModel):
    """Детальный снимок объявления для AdDetailPage."""

    fb_ad_id: str
    ad_name: str | None = None
    campaign_name: str | None = None
    adset_name: str | None = None
    offer_code: str | None = None
    # FSM-состояние UPPERCASE (NORMAL/WARNING_SENT/STOP_SENT/CLAIMED/DISABLED).
    state: str
    snooze_until: str | None = None
    account_id: str | None = None
    can_open_in_ads_manager: bool = False
    metrics: TmaAdMetrics
    recent_alerts: list[TmaRecentAlert] = Field(default_factory=list)


class TmaDisableRequest(BaseModel):
    """Тело POST /tma/ads/{id}/disable."""

    reason: str | None = None
    # Idempotency-токен от клиента: двойной тап с тем же token → одна задача
    # (приоритетнее open_state_token объявления). Пусто → дедуп по инциденту/uuid4.
    token: str | None = None


class TmaDisableResponse(BaseModel):
    """Результат постановки задачи на отключение."""

    ok: bool
    task_id: int | None = None
    channel: str  # всегда 'meta_api' — единственный канал исполнения (Marketing API pause_ad)
    detail: str


class TmaSnoozeRequest(BaseModel):
    """Тело POST /tma/ads/{id}/snooze."""

    minutes: int = Field(..., ge=1, le=1440, description="Снуз в минутах (1..1440)")


class TmaSnoozeResponse(BaseModel):
    """Результат снуза."""

    ok: bool
    snoozed_until: str


class TmaClaimResponse(BaseModel):
    """Результат claim (взять под контроль вручную → alert_state='claimed')."""

    ok: bool
    alert_state: str

# -*- coding: utf-8 -*-
"""Pydantic-схемы для TMA action-endpoint'ов (BL-15 Этап 2).

Все эти endpoint'ы — под Bearer-guard (DepTmaPrincipal). Money-действия
(disable/draft-confirm) меняют реальные объявления, поэтому контракт строгий.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field

from core.tasks.queue import DRAFT_TTL_SECONDS


def _draft_expires_at(created_at_iso: str | None) -> str | None:
    """Вычисляет expires_at = created_at + DRAFT_TTL_SECONDS (ISO-строка).

    Использует константу DRAFT_TTL_SECONDS из core.tasks.queue — единственный
    источник правды о времени жизни draft (24ч, см. cancel_stale_drafts).
    """
    if not created_at_iso:
        return None
    try:
        dt = datetime.fromisoformat(created_at_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt + timedelta(seconds=DRAFT_TTL_SECONDS)).isoformat()
    except (ValueError, TypeError):
        return None


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


class TmaDraftOut(BaseModel):
    """Снимок DRAFT meta-mutation задачи для DraftsPage.

    expires_at: время автоматической отмены (created_at + DRAFT_TTL_SECONDS).
    current_state: текущее состояние объекта мутации (заполняется в detail-endpoint'е).
        - pause_ad / activate_ad: {"alert_state": str, "delivery_status": str | None}
        - set_adset_budget: {"daily_budget_cents": int | None, "lifetime_budget_cents": int | None}
        - bulk_status_change: {"by_state": {"<state>": count}} — агрегат по N объектам
        - Остальные mutation_kind → null (не поддерживаются / слишком дорого).
    В list-endpoint'е current_state = None (дорого резолвить N строк).
    """

    id: int
    mutation_kind: str
    target_id: str | None = None
    ad_account_id: str | None = None
    # payload = MetaMutationPayload.params (kind-specific поля для рендера).
    payload: dict[str, Any] = Field(default_factory=dict)
    requested_by: str
    created_at: str | None = None
    expires_at: str | None = None
    current_state: dict[str, Any] | None = None

    @classmethod
    def from_created_at(cls, *, created_at_iso: str | None, **kwargs) -> "TmaDraftOut":
        """Конструктор с автовычислением expires_at из created_at."""
        return cls(
            created_at=created_at_iso,
            expires_at=_draft_expires_at(created_at_iso),
            **kwargs,
        )


class TmaDraftActionResponse(BaseModel):
    """Результат confirm/reject draft-задачи."""

    ok: bool
    detail: str


class TmaRejectRequest(BaseModel):
    """Тело POST /tma/draft-tasks/{id}/reject."""

    reason: str | None = None

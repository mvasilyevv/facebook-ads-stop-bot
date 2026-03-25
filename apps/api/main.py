# -*- coding: utf-8 -*-
"""Расширенное FastAPI приложение — полный API для UI dashboard.

Включает:
- Настройки (observer, Telegram)
- Управление офферами и правилами
- Dashboard: отключённые объявления, статистика, история алертов
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="FB Stop Bot v2 API", version="0.1.0")

# CORS для React-фронтенда
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================
# Схемы
# ==========================================

class ObserverSettingsSchema(BaseModel):
    """Настройки observer (интервал из UI)."""
    interval_seconds: int = 90
    jitter_seconds: int = 10
    warning_percent_of_stop: Decimal = Decimal("80")


class TelegramSettingsSchema(BaseModel):
    """Настройки Telegram-бота."""
    bot_token: str = ""
    chat_id: str = ""


class OfferSchema(BaseModel):
    """Оффер с CPA."""
    id: str | None = None
    code: str
    name: str
    cpa_amount: Decimal
    is_active: bool = True


class OfferRuleConfigSchema(BaseModel):
    """Конфигурация 6 стоп-правил для оффера."""
    cpc_percent_enabled: bool = True
    cpc_percent_stop: Decimal = Decimal("2")
    cpl_percent_enabled: bool = True
    cpl_percent_stop: Decimal = Decimal("10")
    cpr_percent_enabled: bool = True
    cpr_percent_stop: Decimal = Decimal("20")
    regs_no_dep_enabled: bool = True
    regs_no_dep_stop_count: int = 5
    spend_no_dep_enabled: bool = True
    spend_no_dep_from_percent: Decimal = Decimal("50")
    spend_no_dep_to_percent: Decimal = Decimal("70")
    spend_with_dep_enabled: bool = True
    spend_with_dep_from_percent: Decimal = Decimal("70")
    spend_with_dep_to_percent: Decimal = Decimal("90")


class AdSnapshotSchema(BaseModel):
    """Снимок объявления для dashboard."""
    id: str
    fb_ad_id: str
    campaign_name: str
    adset_name: str
    ad_name: str
    delivery_status: str
    offer_code: str | None = None
    spend: Decimal
    clicks: int
    cpc: Decimal | None = None
    leads: int
    cost_per_lead: Decimal | None = None
    registrations: int
    cost_per_registration: Decimal | None = None
    deposits: int
    alert_state: str
    current_stage: str | None = None
    warning_rule_codes: list[str] = []
    stop_rule_codes: list[str] = []
    last_observed_at: str | None = None


class AlertEventSchema(BaseModel):
    """Запись алерта для истории."""
    id: str
    fb_ad_id: str
    ad_name: str
    stage: str
    state: str
    matched_rule_codes: list[str] = []
    metrics_json: dict = {}
    created_at: str


class DisableTaskSchema(BaseModel):
    """Задача на отключение для мониторинга."""
    id: str
    fb_ad_id: str
    ad_name: str
    status: str
    attempt_count: int
    last_error: str | None = None
    requested_by_username: str | None = None
    created_at: str
    completed_at: str | None = None


class DashboardStatsSchema(BaseModel):
    """Сводная статистика для главной dashboard."""
    total_ads_monitored: int = 0
    ads_in_warning: int = 0
    ads_in_stop: int = 0
    ads_disabled: int = 0
    total_spend: Decimal = Decimal("0")
    active_offers: int = 0
    pending_disable_tasks: int = 0
    last_scan_at: str | None = None


class SpendHistoryPoint(BaseModel):
    """Точка графика расхода."""
    timestamp: str
    spend: Decimal
    clicks: int
    leads: int
    registrations: int
    deposits: int


class HealthResponse(BaseModel):
    status: str = "ok"


# ==========================================
# Эндпоинты — Health
# ==========================================

@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse()


# ==========================================
# Эндпоинты — Настройки
# ==========================================

@app.get("/api/settings/observer", response_model=ObserverSettingsSchema)
async def get_observer_settings():
    """Получить настройки observer (интервал из UI)."""
    # TODO: чтение из БД
    return ObserverSettingsSchema()


@app.put("/api/settings/observer", response_model=ObserverSettingsSchema)
async def update_observer_settings(body: ObserverSettingsSchema):
    """Обновить настройки observer."""
    # TODO: сохранение в БД
    return body


@app.get("/api/settings/telegram", response_model=TelegramSettingsSchema)
async def get_telegram_settings():
    # TODO: чтение из БД
    return TelegramSettingsSchema()


@app.put("/api/settings/telegram", response_model=TelegramSettingsSchema)
async def update_telegram_settings(body: TelegramSettingsSchema):
    # TODO: сохранение в БД
    return body


# ==========================================
# Эндпоинты — Офферы
# ==========================================

@app.get("/api/offers", response_model=list[OfferSchema])
async def list_offers():
    """Список всех офферов."""
    # TODO: чтение из БД
    return []


@app.post("/api/offers", response_model=OfferSchema)
async def create_offer(body: OfferSchema):
    """Создать оффер."""
    # TODO: сохранение в БД
    return body


@app.put("/api/offers/{offer_id}", response_model=OfferSchema)
async def update_offer(offer_id: str, body: OfferSchema):
    """Обновить оффер."""
    # TODO: обновление в БД
    body.id = offer_id
    return body


@app.delete("/api/offers/{offer_id}")
async def delete_offer(offer_id: str):
    """Удалить оффер."""
    # TODO: удаление из БД
    return {"ok": True}


@app.get("/api/offers/{offer_id}/rules", response_model=OfferRuleConfigSchema)
async def get_offer_rules(offer_id: str):
    """Получить правила оффера."""
    # TODO: чтение из БД
    return OfferRuleConfigSchema()


@app.put("/api/offers/{offer_id}/rules", response_model=OfferRuleConfigSchema)
async def update_offer_rules(offer_id: str, body: OfferRuleConfigSchema):
    """Обновить правила оффера."""
    # TODO: сохранение в БД
    return body


# ==========================================
# Эндпоинты — Dashboard
# ==========================================

@app.get("/api/dashboard/stats", response_model=DashboardStatsSchema)
async def get_dashboard_stats():
    """Сводная статистика для главной страницы dashboard."""
    # TODO: агрегация из БД
    return DashboardStatsSchema()


@app.get("/api/dashboard/ads", response_model=list[AdSnapshotSchema])
async def list_ad_snapshots(
    alert_state: str | None = Query(None, description="Фильтр по alert_state (DISABLED, STOP_SENT, ...)"),
    offer_code: str | None = Query(None, description="Фильтр по коду оффера"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Список снимков объявлений (для таблицы в UI).

    Можно фильтровать по alert_state: DISABLED — отключённые,
    STOP_SENT — ожидают действия, WARNING_SENT — предупреждения.
    """
    # TODO: чтение из БД с фильтрами
    return []


@app.get("/api/dashboard/alerts", response_model=list[AlertEventSchema])
async def list_alert_events(
    fb_ad_id: str | None = Query(None),
    stage: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """История алертов (для таблицы и модальных окон)."""
    # TODO: чтение из БД с фильтрами
    return []


@app.get("/api/dashboard/disable-tasks", response_model=list[DisableTaskSchema])
async def list_disable_tasks(
    status: str | None = Query(None, description="Фильтр: PENDING, RUNNING, SUCCEEDED, ..."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Задачи на отключение (для мониторинга)."""
    # TODO: чтение из БД
    return []


@app.get("/api/dashboard/spend-history", response_model=list[SpendHistoryPoint])
async def get_spend_history(
    offer_code: str | None = Query(None),
    hours: int = Query(24, ge=1, le=168),
):
    """История расходов для графика."""
    # TODO: агрегация из БД по временным бакетам
    return []

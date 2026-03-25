# -*- coding: utf-8 -*-
"""FastAPI приложение — полный API для UI dashboard с подключением к БД.

Включает:
- Настройки (observer, Telegram)
- Управление офферами и правилами
- Dashboard: отключённые объявления, статистика, история алертов
"""

from __future__ import annotations

import uuid as _uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_engine, get_session_factory
from core.db.base import Base
from core.domain import AlertState, DisableTaskStatus
from core.models import (
    AdSnapshot,
    AlertEvent,
    DisableTask,
    ObserverSettings,
    Offer,
    OfferRuleConfig,
    TelegramSettings,
)

# ==========================================
# Lifespan — инициализация БД
# ==========================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Создаём таблицы при старте (если нет миграций)."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title="FB Stop Bot v2 API", version="0.1.0", lifespan=lifespan)

# CORS для React-фронтенда
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================
# Dependency — async DB session
# ==========================================


async def get_db() -> AsyncSession:
    """FastAPI dependency: async сессия БД."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


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
async def get_observer_settings(db: AsyncSession = Depends(get_db)):
    """Получить настройки observer."""
    result = await db.execute(
        select(ObserverSettings).where(ObserverSettings.singleton_key == "default")
    )
    row = result.scalar_one_or_none()
    if row is None:
        return ObserverSettingsSchema()
    return ObserverSettingsSchema(
        interval_seconds=row.interval_seconds,
        jitter_seconds=row.jitter_seconds,
        warning_percent_of_stop=row.warning_percent_of_stop,
    )


@app.put("/api/settings/observer", response_model=ObserverSettingsSchema)
async def update_observer_settings(
    body: ObserverSettingsSchema, db: AsyncSession = Depends(get_db)
):
    """Обновить настройки observer (upsert singleton)."""
    result = await db.execute(
        select(ObserverSettings).where(ObserverSettings.singleton_key == "default")
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = ObserverSettings(singleton_key="default")
        db.add(row)
    row.interval_seconds = body.interval_seconds
    row.jitter_seconds = body.jitter_seconds
    row.warning_percent_of_stop = body.warning_percent_of_stop
    await db.commit()
    return body


@app.get("/api/settings/telegram", response_model=TelegramSettingsSchema)
async def get_telegram_settings(db: AsyncSession = Depends(get_db)):
    """Получить настройки Telegram."""
    result = await db.execute(
        select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
    )
    row = result.scalar_one_or_none()
    if row is None:
        return TelegramSettingsSchema()
    return TelegramSettingsSchema(bot_token=row.bot_token, chat_id=row.chat_id)


@app.put("/api/settings/telegram", response_model=TelegramSettingsSchema)
async def update_telegram_settings(
    body: TelegramSettingsSchema, db: AsyncSession = Depends(get_db)
):
    """Обновить настройки Telegram (upsert singleton)."""
    result = await db.execute(
        select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = TelegramSettings(singleton_key="default")
        db.add(row)
    row.bot_token = body.bot_token
    row.chat_id = body.chat_id
    await db.commit()
    return body


# ==========================================
# Эндпоинты — Офферы
# ==========================================


@app.get("/api/offers", response_model=list[OfferSchema])
async def list_offers(db: AsyncSession = Depends(get_db)):
    """Список всех офферов."""
    result = await db.execute(select(Offer).order_by(Offer.created_at.desc()))
    offers = result.scalars().all()
    return [
        OfferSchema(
            id=str(o.id),
            code=o.code,
            name=o.name,
            cpa_amount=o.cpa_amount,
            is_active=o.is_active,
        )
        for o in offers
    ]


@app.post("/api/offers", response_model=OfferSchema, status_code=201)
async def create_offer(body: OfferSchema, db: AsyncSession = Depends(get_db)):
    """Создать оффер."""
    offer = Offer(
        code=body.code,
        name=body.name,
        cpa_amount=body.cpa_amount,
        is_active=body.is_active,
    )
    db.add(offer)
    # Создаём дефолтную конфигурацию правил
    rule_config = OfferRuleConfig(offer_id=offer.id)
    db.add(rule_config)
    await db.commit()
    await db.refresh(offer)
    body.id = str(offer.id)
    return body


@app.put("/api/offers/{offer_id}", response_model=OfferSchema)
async def update_offer(offer_id: str, body: OfferSchema, db: AsyncSession = Depends(get_db)):
    """Обновить оффер."""
    result = await db.execute(select(Offer).where(Offer.id == _uuid.UUID(offer_id)))
    offer = result.scalar_one_or_none()
    if offer is None:
        raise HTTPException(status_code=404, detail="Оффер не найден")
    offer.code = body.code
    offer.name = body.name
    offer.cpa_amount = body.cpa_amount
    offer.is_active = body.is_active
    await db.commit()
    body.id = offer_id
    return body


@app.delete("/api/offers/{offer_id}")
async def delete_offer(offer_id: str, db: AsyncSession = Depends(get_db)):
    """Удалить оффер."""
    result = await db.execute(select(Offer).where(Offer.id == _uuid.UUID(offer_id)))
    offer = result.scalar_one_or_none()
    if offer is None:
        raise HTTPException(status_code=404, detail="Оффер не найден")
    await db.delete(offer)
    await db.commit()
    return {"ok": True}


@app.get("/api/offers/{offer_id}/rules", response_model=OfferRuleConfigSchema)
async def get_offer_rules(offer_id: str, db: AsyncSession = Depends(get_db)):
    """Получить правила оффера."""
    result = await db.execute(
        select(OfferRuleConfig).where(OfferRuleConfig.offer_id == _uuid.UUID(offer_id))
    )
    rc = result.scalar_one_or_none()
    if rc is None:
        return OfferRuleConfigSchema()
    return OfferRuleConfigSchema(
        cpc_percent_enabled=rc.cpc_percent_enabled,
        cpc_percent_stop=rc.cpc_percent_stop,
        cpl_percent_enabled=rc.cpl_percent_enabled,
        cpl_percent_stop=rc.cpl_percent_stop,
        cpr_percent_enabled=rc.cpr_percent_enabled,
        cpr_percent_stop=rc.cpr_percent_stop,
        regs_no_dep_enabled=rc.regs_no_dep_enabled,
        regs_no_dep_stop_count=rc.regs_no_dep_stop_count,
        spend_no_dep_enabled=rc.spend_no_dep_enabled,
        spend_no_dep_from_percent=rc.spend_no_dep_from_percent,
        spend_no_dep_to_percent=rc.spend_no_dep_to_percent,
        spend_with_dep_enabled=rc.spend_with_dep_enabled,
        spend_with_dep_from_percent=rc.spend_with_dep_from_percent,
        spend_with_dep_to_percent=rc.spend_with_dep_to_percent,
    )


@app.put("/api/offers/{offer_id}/rules", response_model=OfferRuleConfigSchema)
async def update_offer_rules(
    offer_id: str, body: OfferRuleConfigSchema, db: AsyncSession = Depends(get_db)
):
    """Обновить правила оффера."""
    uid = _uuid.UUID(offer_id)
    result = await db.execute(select(OfferRuleConfig).where(OfferRuleConfig.offer_id == uid))
    rc = result.scalar_one_or_none()
    if rc is None:
        rc = OfferRuleConfig(offer_id=uid)
        db.add(rc)
    for field in body.model_fields:
        setattr(rc, field, getattr(body, field))
    await db.commit()
    return body


# ==========================================
# Эндпоинты — Dashboard
# ==========================================


@app.get("/api/dashboard/stats", response_model=DashboardStatsSchema)
async def get_dashboard_stats(db: AsyncSession = Depends(get_db)):
    """Сводная статистика для главной страницы dashboard."""
    # Общее количество отслеживаемых объявлений
    total = await db.scalar(select(func.count()).select_from(AdSnapshot)) or 0

    # Подсчёт по состояниям
    warning = (
        await db.scalar(
            select(func.count())
            .select_from(AdSnapshot)
            .where(AdSnapshot.alert_state == AlertState.WARNING_SENT)
        )
        or 0
    )
    stop = (
        await db.scalar(
            select(func.count())
            .select_from(AdSnapshot)
            .where(AdSnapshot.alert_state == AlertState.STOP_SENT)
        )
        or 0
    )
    disabled = (
        await db.scalar(
            select(func.count())
            .select_from(AdSnapshot)
            .where(AdSnapshot.alert_state == AlertState.DISABLED)
        )
        or 0
    )

    # Общий расход
    total_spend = await db.scalar(select(func.coalesce(func.sum(AdSnapshot.spend), 0))) or Decimal(
        "0"
    )

    # Активные офферы
    active_offers = (
        await db.scalar(select(func.count()).select_from(Offer).where(Offer.is_active.is_(True)))
        or 0
    )

    # Задачи на отключение в очереди
    pending_tasks = (
        await db.scalar(
            select(func.count())
            .select_from(DisableTask)
            .where(DisableTask.status.in_([DisableTaskStatus.PENDING, DisableTaskStatus.RETRYING]))
        )
        or 0
    )

    # Последний скан
    last_scan = await db.scalar(select(func.max(AdSnapshot.last_observed_at)))
    last_scan_str = last_scan.isoformat() if last_scan else None

    return DashboardStatsSchema(
        total_ads_monitored=total,
        ads_in_warning=warning,
        ads_in_stop=stop,
        ads_disabled=disabled,
        total_spend=total_spend,
        active_offers=active_offers,
        pending_disable_tasks=pending_tasks,
        last_scan_at=last_scan_str,
    )


@app.get("/api/dashboard/ads", response_model=list[AdSnapshotSchema])
async def list_ad_snapshots(
    alert_state: str | None = Query(None),
    offer_code: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Список снимков объявлений (для таблицы в UI)."""
    q = select(AdSnapshot).order_by(AdSnapshot.last_observed_at.desc())
    if alert_state:
        q = q.where(AdSnapshot.alert_state == AlertState(alert_state))
    if offer_code:
        q = q.where(AdSnapshot.resolved_offer_code == offer_code)
    q = q.limit(limit).offset(offset)

    result = await db.execute(q)
    snapshots = result.scalars().all()
    return [
        AdSnapshotSchema(
            id=str(s.id),
            fb_ad_id=s.fb_ad_id,
            campaign_name=s.campaign_name,
            adset_name=s.adset_name,
            ad_name=s.ad_name,
            delivery_status=s.delivery_status,
            offer_code=s.resolved_offer_code,
            spend=s.spend,
            clicks=s.clicks,
            cpc=s.cpc,
            leads=s.leads,
            cost_per_lead=s.cost_per_lead,
            registrations=s.registrations,
            cost_per_registration=s.cost_per_registration,
            deposits=s.deposits,
            alert_state=s.alert_state.value,
            current_stage=s.current_stage.value if s.current_stage else None,
            warning_rule_codes=s.warning_rule_codes or [],
            stop_rule_codes=s.stop_rule_codes or [],
            last_observed_at=(s.last_observed_at.isoformat() if s.last_observed_at else None),
        )
        for s in snapshots
    ]


@app.get("/api/dashboard/alerts", response_model=list[AlertEventSchema])
async def list_alert_events(
    fb_ad_id: str | None = Query(None),
    stage: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """История алертов (для таблицы и модальных окон)."""
    q = select(AlertEvent).order_by(AlertEvent.created_at.desc())
    if fb_ad_id:
        q = q.where(AlertEvent.fb_ad_id == fb_ad_id)
    if stage:
        from core.domain import AlertStage as AS

        q = q.where(AlertEvent.stage == AS(stage))
    q = q.limit(limit).offset(offset)

    result = await db.execute(q)
    events = result.scalars().all()
    return [
        AlertEventSchema(
            id=str(e.id),
            fb_ad_id=e.fb_ad_id,
            ad_name=e.ad_name,
            stage=e.stage.value,
            state=e.state.value,
            matched_rule_codes=e.matched_rule_codes or [],
            metrics_json=e.metrics_json or {},
            created_at=e.created_at.isoformat(),
        )
        for e in events
    ]


@app.get("/api/dashboard/disable-tasks", response_model=list[DisableTaskSchema])
async def list_disable_tasks(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Задачи на отключение (для мониторинга)."""
    q = select(DisableTask).order_by(DisableTask.created_at.desc())
    if status:
        q = q.where(DisableTask.status == DisableTaskStatus(status))
    q = q.limit(limit).offset(offset)

    result = await db.execute(q)
    tasks = result.scalars().all()
    return [
        DisableTaskSchema(
            id=str(t.id),
            fb_ad_id=t.fb_ad_id,
            ad_name=t.ad_name,
            status=t.status.value,
            attempt_count=t.attempt_count,
            last_error=t.last_error,
            requested_by_username=t.requested_by_username,
            created_at=t.created_at.isoformat(),
            completed_at=t.completed_at.isoformat() if t.completed_at else None,
        )
        for t in tasks
    ]


@app.get("/api/dashboard/spend-history", response_model=list[SpendHistoryPoint])
async def get_spend_history(
    offer_code: str | None = Query(None),
    hours: int = Query(24, ge=1, le=168),
    db: AsyncSession = Depends(get_db),
):
    """История расходов — агрегация из AlertEvent по временным бакетам."""
    # Возвращаем последние снэпшоты как историю
    from datetime import timedelta

    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    q = (
        select(AdSnapshot)
        .where(AdSnapshot.last_observed_at >= cutoff)
        .order_by(AdSnapshot.last_observed_at.asc())
    )
    if offer_code:
        q = q.where(AdSnapshot.resolved_offer_code == offer_code)

    result = await db.execute(q)
    snapshots = result.scalars().all()
    return [
        SpendHistoryPoint(
            timestamp=s.last_observed_at.isoformat(),
            spend=s.spend,
            clicks=s.clicks,
            leads=s.leads,
            registrations=s.registrations,
            deposits=s.deposits,
        )
        for s in snapshots
    ]

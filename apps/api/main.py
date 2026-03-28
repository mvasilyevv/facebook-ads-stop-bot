# -*- coding: utf-8 -*-
"""FastAPI приложение — полный API для UI dashboard с подключением к БД.

Включает:
- Настройки (observer, Telegram)
- Управление офферами и правилами
- Dashboard: отключённые объявления, статистика, история алертов
"""

from __future__ import annotations

import asyncio
import os
import secrets
import signal
import sys
import uuid as _uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.crypto import decrypt, encrypt
from core.config import get_settings
from core.db import get_engine, get_session_factory
from core.db.base import Base
from core.diagnostics import build_ad_quality_diagnostics, compute_cpm_baselines_by_offer
from core.domain import AlertStage, AlertState, DisableTaskStatus
from core.models import (
    AdSnapshot,
    AlertEvent,
    CabinetDayArchive,
    DisableTask,
    ObserverSettings,
    Offer,
    OfferRuleConfig,
    TelegramRecipient,
    TelegramSettings,
    VisionSettings,
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


app = FastAPI(title="FB Stop Bot API", version="0.1.0", lifespan=lifespan)

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
    stop_percent_of_base: Decimal = Decimal("100")
    is_scanning_enabled: bool = True


class ScanningToggleSchema(BaseModel):
    """Схема для быстрого переключения сканирования."""

    enabled: bool


class TelegramSettingsSchema(BaseModel):
    """Настройки Telegram-бота."""

    bot_token: str = ""
    chat_id: str = ""
    is_authorized: bool = False
    bot_username: str = ""
    auth_code: str = ""


class TelegramSetTokenRequest(BaseModel):
    """Запрос на установку bot_token."""

    bot_token: str


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
    early_outbound_ctr_signal_enabled: bool = True
    early_outbound_ctr_signal_min_percent: Decimal = Decimal("0.80")
    early_outbound_ctr_signal_min_spend_percent: Decimal = Decimal("5")
    early_lpv_ratio_signal_enabled: bool = True
    early_lpv_ratio_signal_min_percent: Decimal = Decimal("60")
    early_lpv_ratio_signal_min_outbound_clicks: int = 5
    early_cost_per_lpv_signal_enabled: bool = True
    early_cost_per_lpv_signal_percent_of_cpa: Decimal = Decimal("5")
    early_cost_per_lpv_signal_min_views: int = 2
    frequency_elevated_threshold: Decimal = Decimal("2")
    frequency_critical_threshold: Decimal = Decimal("3")


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
    outbound_clicks: int = 0
    outbound_ctr: Decimal | None = None
    landing_page_views: int = 0
    cost_per_landing_page_view: Decimal | None = None
    cpm: Decimal | None = None
    frequency: Decimal | None = None
    leads: int
    cost_per_lead: Decimal | None = None
    registrations: int
    cost_per_registration: Decimal | None = None
    deposits: int
    alert_state: str
    current_stage: str | None = None
    early_signal_rule_codes: list[str] = []
    warning_rule_codes: list[str] = []
    stop_rule_codes: list[str] = []
    cpm_diagnostic_status: str | None = None
    frequency_diagnostic_status: str | None = None
    diagnostic_short_text: str | None = None
    last_observed_at: str | None = None


class AlertEventSchema(BaseModel):
    """Запись алерта для истории."""

    id: str
    fb_ad_id: str
    ad_name: str
    stage: str
    state: str
    matched_rule_codes: list[str] = []
    reason_title: str | None = None
    reason_text: str | None = None
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
    next_retry_at: str | None = None
    requested_by_username: str | None = None
    created_at: str
    completed_at: str | None = None


class DashboardStatsSchema(BaseModel):
    """Сводная статистика для главной dashboard."""

    total_ads_monitored: int = 0
    active_ads_count: int = 0  # объявления из последней скан-сессии (±30 мин от last_scan_at)
    ads_in_early_signal: int = 0
    ads_in_warning: int = 0
    ads_in_stop: int = 0
    ads_disabled: int = 0
    ads_claimed: int = 0  # CLAIMED — взяты в работу воркером
    ads_disabled_today: int = 0  # успешно отключено сегодня (DisableTask SUCCEEDED)
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


class DashboardPerformanceSummarySchema(BaseModel):
    """Сводка performance-метрик для верхнего ряда."""

    spend: Decimal = Decimal("0")
    clicks: int = 0
    leads: int = 0
    registrations: int = 0
    deposits: int = 0
    cpc: Decimal | None = None
    cpl: Decimal | None = None
    cpr: Decimal | None = None
    spend_per_dep: Decimal | None = None
    click_to_lead_rate: float | None = None
    lead_to_reg_rate: float | None = None
    reg_to_dep_rate: float | None = None


class DashboardPerformanceFunnelStepSchema(BaseModel):
    """Один шаг общей воронки."""

    key: str
    label: str
    count: int
    conversion_rate: float | None = None


class DashboardPerformanceTimelinePointSchema(BaseModel):
    """Точка performance-таймлайна."""

    timestamp: str
    label: str
    spend: Decimal
    registrations: int
    deposits: int


class DashboardPerformanceCampaignSchema(BaseModel):
    """Агрегация performance-метрик по кампании."""

    campaign: str
    spend: Decimal = Decimal("0")
    clicks: int = 0
    leads: int = 0
    registrations: int = 0
    deposits: int = 0
    cpc: Decimal | None = None
    cpl: Decimal | None = None
    cpr: Decimal | None = None
    spend_per_dep: Decimal | None = None
    click_to_lead_rate: float | None = None
    lead_to_reg_rate: float | None = None
    reg_to_dep_rate: float | None = None


class DashboardPerformanceSchema(BaseModel):
    """Полный performance-срез для гибридного dashboard."""

    period: str = "today"
    summary: DashboardPerformanceSummarySchema = DashboardPerformanceSummarySchema()
    funnel: list[DashboardPerformanceFunnelStepSchema] = []
    timeline: list[DashboardPerformanceTimelinePointSchema] = []
    campaigns: list[DashboardPerformanceCampaignSchema] = []


class ChartDataSchema(BaseModel):
    """Данные для графиков на главной странице."""

    alerts_by_hour: list[dict] = []
    rule_violations: list[dict] = []
    campaigns: list[dict] = []
    state_distribution: list[dict] = []
    top_ads_by_spend: list[dict] = []


class HealthResponse(BaseModel):
    status: str = "ok"


class MetricDiagnosticSchema(BaseModel):
    """Диагностика одной метрики для карточки объявления."""

    status: str
    label: str
    text: str
    bar_percent: int
    value: Decimal | None = None
    baseline: Decimal | None = None
    ratio_percent: Decimal | None = None
    elevated_threshold: Decimal | None = None
    critical_threshold: Decimal | None = None


class AdDiagnosticsSchema(BaseModel):
    """Диагностика качества трафика по объявлению."""

    cpm: MetricDiagnosticSchema
    frequency: MetricDiagnosticSchema
    summary_text: str


class VisionSettingsSchema(BaseModel):
    """Настройки Vision браузера."""

    api_url: str = "http://127.0.0.1:3030"
    x_token: str = ""  # маскируется при GET
    profile_id: str = ""
    has_token: bool = False


class VisionSettingsUpdateSchema(BaseModel):
    """Запрос на обновление Vision настроек."""

    api_url: str = "http://127.0.0.1:3030"
    x_token: str = ""  # пустая строка = не менять токен
    profile_id: str = ""


class TelegramRecipientSchema(BaseModel):
    """Получатель Telegram-уведомлений."""

    id: str
    chat_id: str
    username: str = ""
    first_name: str = ""
    is_active: bool = True
    created_at: str


class InviteCodeResponse(BaseModel):
    """Ответ с одноразовым кодом для добавления получателя."""

    code: str
    bot_username: str = ""


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
        stop_percent_of_base=row.stop_percent_of_base,
        is_scanning_enabled=row.is_scanning_enabled,
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
    row.stop_percent_of_base = min(Decimal("100"), max(Decimal("1"), Decimal(body.stop_percent_of_base)))
    row.is_scanning_enabled = body.is_scanning_enabled
    await db.commit()
    return ObserverSettingsSchema(
        interval_seconds=row.interval_seconds,
        jitter_seconds=row.jitter_seconds,
        warning_percent_of_stop=row.warning_percent_of_stop,
        stop_percent_of_base=row.stop_percent_of_base,
        is_scanning_enabled=row.is_scanning_enabled,
    )


@app.patch("/api/settings/observer/scanning")
async def toggle_scanning(body: ScanningToggleSchema, db: AsyncSession = Depends(get_db)):
    """Быстрое переключение сканирования без изменения остальных настроек."""
    result = await db.execute(
        select(ObserverSettings).where(ObserverSettings.singleton_key == "default")
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = ObserverSettings(singleton_key="default")
        db.add(row)
    row.is_scanning_enabled = body.enabled
    await db.commit()
    return {"is_scanning_enabled": row.is_scanning_enabled}


@app.post("/api/settings/observer/scan-now")
async def trigger_scan_now(db: AsyncSession = Depends(get_db)):
    """Установить флаг немедленного скана — воркер выполнит скан при следующей проверке."""
    result = await db.execute(
        select(ObserverSettings).where(ObserverSettings.singleton_key == "default")
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = ObserverSettings(singleton_key="default")
        db.add(row)
    row.scan_requested = True
    await db.commit()
    return {"scan_requested": True}


@app.post("/api/observer/restart")
async def restart_observer():
    """Перезапуск observer worker: завершает текущий процесс и запускает новый."""
    project_root = Path(__file__).parent.parent.parent
    pid_file = project_root / ".logs" / "pids.txt"
    log_file = project_root / ".logs" / "observer.log"
    run_script = project_root / "run_observer.py"

    # Находим и завершаем текущий процесс воркера
    old_pid: int | None = None
    if pid_file.exists():
        lines = pid_file.read_text().splitlines()
        remaining = []
        for line in lines:
            parts = line.strip().split()
            if len(parts) == 2 and parts[1] == "observer":
                try:
                    old_pid = int(parts[0])
                except ValueError:
                    pass
            else:
                remaining.append(line)

        if old_pid:
            try:
                os.kill(old_pid, signal.SIGTERM)
                # Даём время на graceful shutdown
                await asyncio.sleep(2.0)
                # Проверяем что процесс завершился
                try:
                    os.kill(old_pid, 0)
                    # Всё ещё жив — SIGKILL
                    os.kill(old_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            except ProcessLookupError:
                pass  # Процесс уже не существует
            pid_file.write_text("\n".join(remaining) + "\n" if remaining else "")

    # Запускаем новый процесс
    venv_python = project_root / ".venv" / "bin" / "python"
    python_bin = str(venv_python) if venv_python.exists() else sys.executable

    with open(log_file, "a") as log:
        log.write(f"\n--- Перезапуск воркера через UI {datetime.now(UTC).isoformat()} ---\n")

    proc = await asyncio.create_subprocess_exec(
        python_bin,
        str(run_script),
        stdout=open(log_file, "a"),
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(project_root),
    )

    # Сохраняем новый PID
    with open(pid_file, "a") as f:
        f.write(f"{proc.pid} observer\n")

    return {"restarted": True, "old_pid": old_pid, "new_pid": proc.pid}


@app.get("/api/settings/telegram", response_model=TelegramSettingsSchema)
async def get_telegram_settings(db: AsyncSession = Depends(get_db)):
    """Получить настройки Telegram (токен маскируется)."""
    result = await db.execute(
        select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
    )
    row = result.scalar_one_or_none()
    if row is None:
        # Fallback на .env
        from core.config import get_settings
        s = get_settings()
        token = s.telegram_bot_token
        masked = (token[:10] + "***") if len(token) > 10 else ("***" if token else "")
        return TelegramSettingsSchema(bot_token=masked, chat_id=s.telegram_chat_id)

    token = decrypt(row.bot_token_encrypted) if row.bot_token_encrypted else ""
    masked = (token[:10] + "***") if len(token) > 10 else ("***" if token else "")
    return TelegramSettingsSchema(
        bot_token=masked,
        chat_id=row.chat_id,
        is_authorized=row.is_authorized,
        bot_username=row.bot_username,
        auth_code=row.auth_code if not row.is_authorized else "",
    )


@app.put("/api/settings/telegram/token")
async def set_telegram_token(
    body: TelegramSetTokenRequest, db: AsyncSession = Depends(get_db)
):
    """Установить bot_token: проверяет через getMe, генерирует auth_code."""
    import httpx

    token = body.bot_token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Токен не может быть пустым")

    # Проверяем токен через Telegram API getMe
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            data = resp.json()
            if not data.get("ok"):
                raise HTTPException(status_code=400, detail="Невалидный токен бота")
            bot_info = data["result"]
            bot_username = bot_info.get("username", "")
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=400, detail="Не удалось подключиться к Telegram API"
        ) from exc

    # Генерируем 6-значный код авторизации
    auth_code = str(secrets.randbelow(900000) + 100000)

    # Upsert в БД
    result = await db.execute(
        select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = TelegramSettings(singleton_key="default")
        db.add(row)

    row.bot_token_encrypted = encrypt(token)
    row.auth_code = auth_code
    row.is_authorized = False
    row.chat_id = ""
    row.bot_username = bot_username
    await db.commit()

    return {
        "bot_username": bot_username,
        "auth_code": auth_code,
        "message": f"Отправьте боту @{bot_username} команду: /start {auth_code}",
    }


@app.delete("/api/settings/telegram")
async def revoke_telegram(db: AsyncSession = Depends(get_db)):
    """Отозвать авторизацию Telegram — сбрасывает все настройки."""
    result = await db.execute(
        select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
    )
    row = result.scalar_one_or_none()
    if row is not None:
        row.bot_token_encrypted = ""
        row.chat_id = ""
        row.is_authorized = False
        row.auth_code = ""
        row.bot_username = ""
        await db.commit()
    return {"status": "ok"}


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
    await db.flush()
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
        early_outbound_ctr_signal_enabled=rc.early_outbound_ctr_signal_enabled,
        early_outbound_ctr_signal_min_percent=rc.early_outbound_ctr_signal_min_percent,
        early_outbound_ctr_signal_min_spend_percent=rc.early_outbound_ctr_signal_min_spend_percent,
        early_lpv_ratio_signal_enabled=rc.early_lpv_ratio_signal_enabled,
        early_lpv_ratio_signal_min_percent=rc.early_lpv_ratio_signal_min_percent,
        early_lpv_ratio_signal_min_outbound_clicks=rc.early_lpv_ratio_signal_min_outbound_clicks,
        early_cost_per_lpv_signal_enabled=rc.early_cost_per_lpv_signal_enabled,
        early_cost_per_lpv_signal_percent_of_cpa=rc.early_cost_per_lpv_signal_percent_of_cpa,
        early_cost_per_lpv_signal_min_views=rc.early_cost_per_lpv_signal_min_views,
        frequency_elevated_threshold=rc.frequency_elevated_threshold,
        frequency_critical_threshold=rc.frequency_critical_threshold,
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
    for field, value in body.model_dump().items():
        setattr(rc, field, value)
    await db.commit()
    return body


# ==========================================
# Helpers — Dashboard performance
# ==========================================


def _performance_cutoff(period: str, now: datetime) -> datetime:
    """Возвращает нижнюю границу периода для performance-дашборда."""
    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "7d":
        return now - timedelta(days=7)
    if period == "30d":
        return now - timedelta(days=30)
    raise ValueError(f"Неизвестный период: {period}")


def _current_scan_cutoff(last_scan: datetime | None) -> datetime:
    """Возвращает начало актуальной скан-сессии."""
    if last_scan is None:
        return datetime.now(UTC)
    return last_scan - timedelta(minutes=30)


def _safe_decimal_div(numerator: Decimal, denominator: int) -> Decimal | None:
    """Безопасно делит Decimal на целое число для cost-метрик."""
    if denominator <= 0:
        return None
    return (Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.0001"))


def _safe_percent(numerator: int, denominator: int) -> float | None:
    """Возвращает конверсию в процентах или None, если делить нельзя."""
    if denominator <= 0:
        return None
    return round((float(numerator) / float(denominator)) * 100, 1)


@lru_cache(maxsize=1)
def _dashboard_timezone() -> ZoneInfo:
    """Часовой пояс dashboard для локальных суточных срезов."""
    return ZoneInfo(get_settings().app_timezone)


def _dashboard_now() -> datetime:
    """Текущее время в часовом поясе dashboard."""
    return datetime.now(_dashboard_timezone())


def _to_dashboard_timezone(value: datetime) -> datetime:
    """Переводит дату в локальный часовой пояс dashboard."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(_dashboard_timezone())


async def _get_cabinet_day_start(db: AsyncSession) -> datetime | None:
    """Возвращает зафиксированное начало текущих суток кабинета."""
    row = await db.scalar(
        select(ObserverSettings.cabinet_day_started_at).where(
            ObserverSettings.singleton_key == "default"
        )
    )
    return row


def _timeline_bucket_start(value: datetime, period: str) -> datetime:
    """Нормализует время до начала бакета."""
    if period in {"7d", "30d"}:
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    return value.replace(minute=0, second=0, microsecond=0)


def _timeline_bucket_step(period: str) -> timedelta:
    """Шаг бакета для таймлайна."""
    return timedelta(days=1) if period in {"7d", "30d"} else timedelta(hours=1)


def _timeline_bucket_label(value: datetime, period: str) -> str:
    """Подпись бакета для UI."""
    return value.strftime("%d.%m") if period in {"7d", "30d"} else value.strftime("%H:00")


def _build_performance_summary(
    *,
    spend: Decimal,
    clicks: int,
    leads: int,
    registrations: int,
    deposits: int,
) -> DashboardPerformanceSummarySchema:
    """Собирает сводный блок performance-метрик."""
    return DashboardPerformanceSummarySchema(
        spend=spend,
        clicks=clicks,
        leads=leads,
        registrations=registrations,
        deposits=deposits,
        cpc=_safe_decimal_div(spend, clicks),
        cpl=_safe_decimal_div(spend, leads),
        cpr=_safe_decimal_div(spend, registrations),
        spend_per_dep=_safe_decimal_div(spend, deposits),
        click_to_lead_rate=_safe_percent(leads, clicks),
        lead_to_reg_rate=_safe_percent(registrations, leads),
        reg_to_dep_rate=_safe_percent(deposits, registrations),
    )


def _json_decimal(value: object | None) -> Decimal:
    """Преобразует число из JSON/ORM в Decimal без падения."""
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _json_int(value: object | None) -> int:
    """Преобразует число из JSON/ORM в int без падения."""
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except Exception:
        return 0


def _accumulate_campaign_metrics(
    campaign_map: dict[str, dict[str, object]],
    *,
    campaign_name: str,
    spend: Decimal,
    clicks: int,
    leads: int,
    registrations: int,
    deposits: int,
) -> None:
    """Накапливает агрегаты по кампании из разных источников истории."""
    if not campaign_name:
        return
    row = campaign_map.setdefault(
        campaign_name,
        {
            "campaign": campaign_name,
            "spend": Decimal("0"),
            "clicks": 0,
            "leads": 0,
            "registrations": 0,
            "deposits": 0,
        },
    )
    row["spend"] += spend
    row["clicks"] += clicks
    row["leads"] += leads
    row["registrations"] += registrations
    row["deposits"] += deposits


def _finalize_campaign_rows(
    campaign_map: dict[str, dict[str, object]],
) -> list[DashboardPerformanceCampaignSchema]:
    """Преобразует накопленную карту кампаний в схемы API."""
    return [
        DashboardPerformanceCampaignSchema(
            campaign=str(row["campaign"]),
            spend=Decimal(row["spend"]),
            clicks=int(row["clicks"]),
            leads=int(row["leads"]),
            registrations=int(row["registrations"]),
            deposits=int(row["deposits"]),
            cpc=_safe_decimal_div(Decimal(row["spend"]), int(row["clicks"])),
            cpl=_safe_decimal_div(Decimal(row["spend"]), int(row["leads"])),
            cpr=_safe_decimal_div(Decimal(row["spend"]), int(row["registrations"])),
            spend_per_dep=_safe_decimal_div(Decimal(row["spend"]), int(row["deposits"])),
            click_to_lead_rate=_safe_percent(int(row["leads"]), int(row["clicks"])),
            lead_to_reg_rate=_safe_percent(int(row["registrations"]), int(row["leads"])),
            reg_to_dep_rate=_safe_percent(int(row["deposits"]), int(row["registrations"])),
        )
        for row in sorted(campaign_map.values(), key=lambda item: item["spend"], reverse=True)
    ]


def _build_dashboard_performance_payload(
    snapshots: list[AdSnapshot],
    *,
    period: str,
    now: datetime | None = None,
    cutoff: datetime | None = None,
    archives: list[CabinetDayArchive] | None = None,
) -> DashboardPerformanceSchema:
    """Агрегирует performance-данные из текущего дня и архива суток кабинета."""
    current_time = now or _dashboard_now()
    cutoff = cutoff or _performance_cutoff(period, current_time)
    archives = archives or []
    relevant = [
        snapshot
        for snapshot in snapshots
        if snapshot.last_observed_at and _to_dashboard_timezone(snapshot.last_observed_at) >= cutoff
    ]

    step = _timeline_bucket_step(period)
    bucket_cursor = _timeline_bucket_start(cutoff, period)
    last_bucket = _timeline_bucket_start(current_time, period)
    timeline_map: dict[datetime, dict] = {}
    while bucket_cursor <= last_bucket:
        timeline_map[bucket_cursor] = {
            "timestamp": bucket_cursor.isoformat(),
            "label": _timeline_bucket_label(bucket_cursor, period),
            "spend": Decimal("0"),
            "registrations": 0,
            "deposits": 0,
        }
        bucket_cursor += step

    total_spend = Decimal("0")
    total_clicks = 0
    total_leads = 0
    total_regs = 0
    total_deps = 0
    campaign_map: dict[str, dict[str, object]] = {}

    for archive in archives:
        summary = archive.summary_json or {}
        spend = _json_decimal(summary.get("spend"))
        clicks = _json_int(summary.get("clicks"))
        leads = _json_int(summary.get("leads"))
        registrations = _json_int(summary.get("registrations"))
        deposits = _json_int(summary.get("deposits"))

        total_spend += spend
        total_clicks += clicks
        total_leads += leads
        total_regs += registrations
        total_deps += deposits

        bucket_source = archive.started_at or archive.ended_at or archive.reset_detected_at
        if bucket_source is not None:
            bucket = _timeline_bucket_start(_to_dashboard_timezone(bucket_source), period)
            if bucket in timeline_map:
                timeline_map[bucket]["spend"] += spend
                timeline_map[bucket]["registrations"] += registrations
                timeline_map[bucket]["deposits"] += deposits

        for row in archive.campaigns_json or []:
            _accumulate_campaign_metrics(
                campaign_map,
                campaign_name=str(row.get("campaign") or "").strip(),
                spend=_json_decimal(row.get("spend")),
                clicks=_json_int(row.get("clicks")),
                leads=_json_int(row.get("leads")),
                registrations=_json_int(row.get("registrations")),
                deposits=_json_int(row.get("deposits")),
            )

    for snapshot in relevant:
        spend = Decimal(snapshot.spend or 0)
        clicks = int(snapshot.clicks or 0)
        leads = int(snapshot.leads or 0)
        registrations = int(snapshot.registrations or 0)
        deposits = int(snapshot.deposits or 0)

        total_spend += spend
        total_clicks += clicks
        total_leads += leads
        total_regs += registrations
        total_deps += deposits

        bucket = _timeline_bucket_start(_to_dashboard_timezone(snapshot.last_observed_at), period)
        if bucket in timeline_map:
            timeline_map[bucket]["spend"] += spend
            timeline_map[bucket]["registrations"] += registrations
            timeline_map[bucket]["deposits"] += deposits

        campaign_name = (snapshot.campaign_name or "").strip()
        _accumulate_campaign_metrics(
            campaign_map,
            campaign_name=campaign_name,
            spend=spend,
            clicks=clicks,
            leads=leads,
            registrations=registrations,
            deposits=deposits,
        )

    funnel = [
        DashboardPerformanceFunnelStepSchema(key="clicks", label="Клики", count=total_clicks),
        DashboardPerformanceFunnelStepSchema(
            key="leads",
            label="Лиды",
            count=total_leads,
            conversion_rate=_safe_percent(total_leads, total_clicks),
        ),
        DashboardPerformanceFunnelStepSchema(
            key="registrations",
            label="Реги",
            count=total_regs,
            conversion_rate=_safe_percent(total_regs, total_leads),
        ),
        DashboardPerformanceFunnelStepSchema(
            key="deposits",
            label="Депозиты",
            count=total_deps,
            conversion_rate=_safe_percent(total_deps, total_regs),
        ),
    ]

    campaigns = _finalize_campaign_rows(campaign_map)
    timeline = [
        DashboardPerformanceTimelinePointSchema(**row)
        for _, row in sorted(timeline_map.items(), key=lambda item: item[0])
    ]

    return DashboardPerformanceSchema(
        period=period,
        summary=_build_performance_summary(
            spend=total_spend,
            clicks=total_clicks,
            leads=total_leads,
            registrations=total_regs,
            deposits=total_deps,
        ),
        funnel=funnel,
        timeline=timeline,
        campaigns=campaigns,
    )


async def _resolve_dashboard_snapshot_cutoff(
    db: AsyncSession,
) -> datetime:
    """Возвращает границу актуальной скан-сессии для текущих snapshot-ов."""
    last_scan = await db.scalar(select(func.max(AdSnapshot.last_observed_at)))
    return _current_scan_cutoff(last_scan)


async def _resolve_dashboard_event_cutoff(
    db: AsyncSession,
    *,
    period: str,
    now: datetime,
) -> datetime:
    """Возвращает границу периода для событий и rule-history."""
    if period != "today":
        return _performance_cutoff(period, now)
    cabinet_day_start = await _get_cabinet_day_start(db)
    if cabinet_day_start is not None:
        return cabinet_day_start
    last_archive_end = await db.scalar(select(func.max(CabinetDayArchive.ended_at)))
    if last_archive_end is not None:
        return last_archive_end
    # Временный fallback, пока observer ещё не зафиксировал zero-scan для новых суток.
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def _load_dashboard_archives(
    db: AsyncSession,
    *,
    cutoff: datetime,
) -> list[CabinetDayArchive]:
    """Загружает архивы завершённых суток кабинета, попадающие в период."""
    result = await db.execute(
        select(CabinetDayArchive)
        .where(CabinetDayArchive.ended_at >= cutoff)
        .order_by(CabinetDayArchive.started_at.asc())
    )
    return result.scalars().all()


async def _load_frequency_thresholds_by_offer(
    db: AsyncSession,
    *,
    offer_codes: set[str],
) -> dict[str, tuple[Decimal, Decimal]]:
    """Загружает пороги Frequency по кодам офферов."""
    if not offer_codes:
        return {}

    result = await db.execute(
        select(
            Offer.code,
            OfferRuleConfig.frequency_elevated_threshold,
            OfferRuleConfig.frequency_critical_threshold,
        )
        .join(OfferRuleConfig, OfferRuleConfig.offer_id == Offer.id)
        .where(Offer.code.in_(offer_codes))
    )
    return {
        code: (Decimal(elevated), Decimal(critical))
        for code, elevated, critical in result.all()
    }


async def _build_snapshot_diagnostics_map(
    db: AsyncSession,
    snapshots: list[AdSnapshot],
) -> dict[str, AdDiagnosticsSchema]:
    """Строит диагностику CPM/Frequency для набора снэпшотов."""
    if not snapshots:
        return {}

    scan_cutoff = await _resolve_dashboard_snapshot_cutoff(db)
    active_result = await db.execute(
        select(AdSnapshot)
        .where(
            AdSnapshot.last_observed_at >= scan_cutoff,
            AdSnapshot.delivery_status != "OFF",
        )
        .order_by(AdSnapshot.last_observed_at.desc())
    )
    active_snapshots = active_result.scalars().all()
    cpm_baselines = compute_cpm_baselines_by_offer(
        [snapshot for snapshot in active_snapshots if snapshot.resolved_offer_code],
        offer_code_getter=lambda snapshot: snapshot.resolved_offer_code,
        cpm_getter=lambda snapshot: snapshot.cpm,
    )
    frequency_thresholds = await _load_frequency_thresholds_by_offer(
        db,
        offer_codes={snapshot.resolved_offer_code for snapshot in snapshots if snapshot.resolved_offer_code},
    )

    diagnostics_map: dict[str, AdDiagnosticsSchema] = {}
    for snapshot in snapshots:
        elevated_threshold, critical_threshold = frequency_thresholds.get(
            snapshot.resolved_offer_code or "",
            (Decimal("2"), Decimal("3")),
        )
        diagnostics = build_ad_quality_diagnostics(
            cpm_value=snapshot.cpm,
            cpm_baseline=cpm_baselines.get(snapshot.resolved_offer_code or ""),
            frequency_value=snapshot.frequency,
            frequency_elevated_threshold=elevated_threshold,
            frequency_critical_threshold=critical_threshold,
        )
        diagnostics_map[snapshot.fb_ad_id] = AdDiagnosticsSchema(**diagnostics.as_dict())

    return diagnostics_map


# ==========================================
# Эндпоинты — Dashboard
# ==========================================


@app.get("/api/dashboard/stats", response_model=DashboardStatsSchema)
async def get_dashboard_stats(db: AsyncSession = Depends(get_db)):
    """Сводная статистика для главной страницы dashboard.

    Все счётчики — только по объявлениям из текущей скан-сессии
    (виденным в течение 30 минут от последнего скана).
    """
    # Определяем границу текущей скан-сессии
    last_scan = await db.scalar(select(func.max(AdSnapshot.last_observed_at)))
    last_scan_str = last_scan.isoformat() if last_scan else None
    scan_cutoff = _current_scan_cutoff(last_scan)

    # Все счётчики — один GROUP BY только по текущей сессии
    state_stats = await db.execute(
        select(
            AdSnapshot.alert_state,
            func.count().label("cnt"),
            func.coalesce(func.sum(AdSnapshot.spend), 0).label("spend"),
        )
        .where(AdSnapshot.last_observed_at >= scan_cutoff)
        .group_by(AdSnapshot.alert_state)
    )
    rows = state_stats.all()

    total = 0
    early_signal = 0
    warning = 0
    stop = 0
    disabled = 0
    claimed = 0
    total_spend = Decimal("0")
    for state, cnt, spend in rows:
        total += cnt
        total_spend += spend or Decimal("0")
        if state == AlertState.EARLY_SIGNAL_SENT:
            early_signal = cnt
        elif state == AlertState.WARNING_SENT:
            warning = cnt
        elif state == AlertState.STOP_SENT:
            stop = cnt
        elif state == AlertState.DISABLED:
            disabled = cnt
        elif state == AlertState.CLAIMED:
            claimed = cnt

    # Активные офферы + задачи на отключение
    cabinet_day_start = await _get_cabinet_day_start(db)
    # Если zero-scan ещё ни разу не был зафиксирован, считаем только по актуальной скан-сессии,
    # чтобы не тащить вчерашние значения из календарной полуночи.
    disabled_since = cabinet_day_start or scan_cutoff
    active_offers = (
        await db.scalar(select(func.count()).select_from(Offer).where(Offer.is_active.is_(True)))
        or 0
    )
    pending_tasks = (
        await db.scalar(
            select(func.count())
            .select_from(DisableTask)
            .where(DisableTask.status.in_([DisableTaskStatus.PENDING, DisableTaskStatus.RETRYING, DisableTaskStatus.RUNNING]))
        )
        or 0
    )
    disabled_today = (
        await db.scalar(
            select(func.count())
            .select_from(DisableTask)
            .where(
                DisableTask.status == DisableTaskStatus.SUCCEEDED,
                DisableTask.completed_at >= disabled_since,
            )
        )
        or 0
    )

    return DashboardStatsSchema(
        total_ads_monitored=total,
        active_ads_count=total,
        ads_in_early_signal=early_signal,
        ads_in_warning=warning,
        ads_in_stop=stop,
        ads_disabled=disabled,
        ads_claimed=claimed,
        ads_disabled_today=disabled_today,
        total_spend=total_spend,
        active_offers=active_offers,
        pending_disable_tasks=pending_tasks,
        last_scan_at=last_scan_str,
    )


@app.get("/api/dashboard/performance", response_model=DashboardPerformanceSchema)
async def get_dashboard_performance(
    period: str = Query("today", pattern="^(today|7d|30d)$"),
    db: AsyncSession = Depends(get_db),
):
    """Performance-срез для гибридного dashboard."""
    now = _dashboard_now()
    snapshot_cutoff = await _resolve_dashboard_snapshot_cutoff(db)
    cutoff = snapshot_cutoff if period == "today" else _performance_cutoff(period, now)
    result = await db.execute(
        select(AdSnapshot)
        .where(AdSnapshot.last_observed_at >= snapshot_cutoff)
        .order_by(AdSnapshot.last_observed_at.asc())
    )
    snapshots = result.scalars().all()
    archives = []
    if period != "today":
        archives = await _load_dashboard_archives(db, cutoff=cutoff)
    return _build_dashboard_performance_payload(
        snapshots,
        period=period,
        now=now,
        cutoff=cutoff,
        archives=archives,
    )


@app.get("/api/dashboard/ads", response_model=list[AdSnapshotSchema])
async def list_ad_snapshots(
    alert_state: str | None = Query(None),
    offer_code: str | None = Query(None),
    since_hours: int | None = Query(None, ge=1, le=168),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Список снимков объявлений (для таблицы в UI).

    since_hours — фильтр по last_observed_at: только объявления, виденные за
    последние N часов (None = все).
    """
    q = select(AdSnapshot).order_by(AdSnapshot.last_observed_at.desc())
    if alert_state:
        q = q.where(AdSnapshot.alert_state == AlertState(alert_state))
    if offer_code:
        q = q.where(AdSnapshot.resolved_offer_code == offer_code)
    if since_hours is not None:
        cutoff = datetime.now(UTC) - timedelta(hours=since_hours)
        q = q.where(AdSnapshot.last_observed_at >= cutoff)
    q = q.limit(limit).offset(offset)

    result = await db.execute(q)
    snapshots = result.scalars().all()
    diagnostics_map = await _build_snapshot_diagnostics_map(db, snapshots)
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
            outbound_clicks=s.outbound_clicks,
            outbound_ctr=s.outbound_ctr,
            landing_page_views=s.landing_page_views,
            cost_per_landing_page_view=s.cost_per_landing_page_view,
            cpm=s.cpm,
            frequency=s.frequency,
            leads=s.leads,
            cost_per_lead=s.cost_per_lead,
            registrations=s.registrations,
            cost_per_registration=s.cost_per_registration,
            deposits=s.deposits,
            alert_state=s.alert_state.value,
            current_stage=s.current_stage.value if s.current_stage else None,
            early_signal_rule_codes=s.early_signal_rule_codes or [],
            warning_rule_codes=s.warning_rule_codes or [],
            stop_rule_codes=s.stop_rule_codes or [],
            cpm_diagnostic_status=diagnostics_map[s.fb_ad_id].cpm.status if s.fb_ad_id in diagnostics_map else None,
            frequency_diagnostic_status=(
                diagnostics_map[s.fb_ad_id].frequency.status if s.fb_ad_id in diagnostics_map else None
            ),
            diagnostic_short_text=(
                diagnostics_map[s.fb_ad_id].summary_text if s.fb_ad_id in diagnostics_map else None
            ),
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
            reason_title=e.reason_title,
            reason_text=e.reason_text,
            metrics_json=e.metrics_json or {},
            created_at=e.created_at.isoformat(),
        )
        for e in events
    ]


@app.post("/api/dashboard/disable-tasks/{task_id}/retry")
async def retry_disable_task(task_id: str, db: AsyncSession = Depends(get_db)):
    """Принудительный повтор задачи на отключение — сбрасывает таймер retry."""
    result = await db.execute(select(DisableTask).where(DisableTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if task.status not in (DisableTaskStatus.RETRYING, DisableTaskStatus.FAILED):
        raise HTTPException(status_code=400, detail="Задача не в состоянии retry/failed")
    task.status = DisableTaskStatus.PENDING
    task.next_retry_at = None
    task.last_error = None
    await db.commit()
    return {"ok": True}


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
            next_retry_at=t.next_retry_at.isoformat() if t.next_retry_at else None,
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


@app.get("/api/dashboard/chart-data", response_model=ChartDataSchema)
async def get_chart_data(
    period: str = Query("today", pattern="^(today|7d|30d)$"),
    db: AsyncSession = Depends(get_db),
):
    """Данные для операционной аналитики dashboard с учётом выбранного периода."""
    now = _dashboard_now()
    snapshot_cutoff = await _resolve_dashboard_snapshot_cutoff(db)
    history_cutoff = snapshot_cutoff if period == "today" else _performance_cutoff(period, now)
    event_cutoff = await _resolve_dashboard_event_cutoff(db, period=period, now=now)

    # 1. Алерты по выбранному периоду
    alerts_result = await db.execute(
        select(AlertEvent.stage, AlertEvent.created_at)
        .where(AlertEvent.created_at >= event_cutoff)
        .order_by(AlertEvent.created_at.asc())
    )
    alert_rows = alerts_result.all()

    alerts_timeline: dict[datetime, dict] = {}
    bucket_cursor = _timeline_bucket_start(event_cutoff, period)
    last_bucket = _timeline_bucket_start(now, period)
    while bucket_cursor <= last_bucket:
        label = _timeline_bucket_label(bucket_cursor, period)
        alerts_timeline[bucket_cursor] = {
            "hour": label,
            "label": label,
            "early_signal": 0,
            "warning": 0,
            "stop": 0,
        }
        bucket_cursor += _timeline_bucket_step(period)
    for stage, created_at in alert_rows:
        bucket = _timeline_bucket_start(_to_dashboard_timezone(created_at), period)
        if bucket in alerts_timeline:
            if stage == AlertStage.EARLY_SIGNAL:
                alerts_timeline[bucket]["early_signal"] += 1
            elif stage == AlertStage.WARNING:
                alerts_timeline[bucket]["warning"] += 1
            elif stage == AlertStage.STOP:
                alerts_timeline[bucket]["stop"] += 1
    alerts_by_hour = [
        row for _, row in sorted(alerts_timeline.items(), key=lambda item: item[0])
    ]

    # 2. Нарушения правил за выбранный период
    _rule_labels = {
        "cpc_stop": "Дорогой клик",
        "cpl_stop": "Дорогой лид",
        "cpr_stop": "Дорогая рега",
        "regs_no_dep_stop": "Реги без депозитов",
        "spend_no_dep_range": "Расход без депа",
        "spend_with_dep_range": "Расход с депозитом",
    }
    rules_result = await db.execute(
        select(AlertEvent.matched_rule_codes).where(AlertEvent.created_at >= event_cutoff)
    )
    rule_counts: dict[str, int] = {}
    for (codes,) in rules_result.all():
        if codes:
            for code in codes:
                rule_counts[code] = rule_counts.get(code, 0) + 1
    rule_violations = sorted(
        [{"rule": _rule_labels.get(k, k), "count": v} for k, v in rule_counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )

    # 3. Кампании за период собираем тем же способом, что верхний performance-блок.
    snapshot_result = await db.execute(
        select(AdSnapshot)
        .where(AdSnapshot.last_observed_at >= snapshot_cutoff)
        .order_by(AdSnapshot.last_observed_at.asc())
    )
    snapshots = snapshot_result.scalars().all()
    archives = []
    if period != "today":
        archives = await _load_dashboard_archives(db, cutoff=history_cutoff)
    performance_payload = _build_dashboard_performance_payload(
        snapshots,
        period=period,
        now=now,
        cutoff=history_cutoff,
        archives=archives,
    )
    campaigns = [
        {
            "campaign": row.campaign[:30] + "…" if len(row.campaign) > 30 else row.campaign,
            "campaign_full": row.campaign,
            "spend": float(row.spend or 0),
            "deposits": int(row.deposits or 0),
            "leads": int(row.leads or 0),
            "registrations": int(row.registrations or 0),
        }
        for row in performance_payload.campaigns[:10]
    ]

    # 4. Распределение статусов — только по актуальному живому срезу.
    state_result = await db.execute(
        select(AdSnapshot.alert_state, func.count().label("cnt"))
        .where(AdSnapshot.last_observed_at >= snapshot_cutoff)
        .group_by(AdSnapshot.alert_state)
    )
    _state_labels = {
        AlertState.NORMAL: "Норма",
        AlertState.EARLY_SIGNAL_SENT: "Ранний сигнал",
        AlertState.WARNING_SENT: "Предупреждение",
        AlertState.STOP_SENT: "Стоп",
        AlertState.CLAIMED: "Ожидает OFF",
        AlertState.DISABLED: "Отключён",
    }
    state_distribution = [
        {"state": _state_labels.get(state, str(state)), "count": cnt}
        for state, cnt in state_result.all()
    ]

    # 5. Топ объявлений по расходу — текущий живой срез, без исторического режима.
    top_ads_result = await db.execute(
        select(
            AdSnapshot.ad_name,
            AdSnapshot.adset_name,
            AdSnapshot.fb_ad_id,
            AdSnapshot.spend,
            AdSnapshot.cpc,
            AdSnapshot.leads,
            AdSnapshot.deposits,
            AdSnapshot.alert_state,
        )
        .where(AdSnapshot.last_observed_at >= snapshot_cutoff)
        .where(AdSnapshot.spend > 0)
        .order_by(AdSnapshot.spend.desc())
        .limit(8)
    )
    _state_icons = {
        AlertState.EARLY_SIGNAL_SENT: "🔎",
        AlertState.STOP_SENT: "🛑",
        AlertState.WARNING_SENT: "⚠️",
        AlertState.CLAIMED: "🔄",
        AlertState.DISABLED: "🚫",
        AlertState.NORMAL: "✅",
    }
    top_ads_by_spend = [
        {
            "name": row.ad_name[:25] + "…" if len(row.ad_name) > 25 else row.ad_name,
            "name_full": row.ad_name,
            "adset_name": row.adset_name,
            "adset_short": row.adset_name[:18] + "…" if len(row.adset_name) > 18 else row.adset_name,
            "label": (
                f"{row.ad_name[:16] + '…' if len(row.ad_name) > 16 else row.ad_name} · "
                f"{row.adset_name[:10] + '…' if len(row.adset_name) > 10 else row.adset_name}"
            ),
            "fb_ad_id": row.fb_ad_id,
            "spend": float(row.spend or 0),
            "cpc": float(row.cpc) if row.cpc else None,
            "leads": int(row.leads or 0),
            "deposits": int(row.deposits or 0),
            "state": row.alert_state.value if row.alert_state else "NORMAL",
            "state_icon": _state_icons.get(row.alert_state, "✅"),
        }
        for row in top_ads_result.all()
    ]

    return ChartDataSchema(
        alerts_by_hour=alerts_by_hour,
        rule_violations=rule_violations,
        campaigns=campaigns,
        state_distribution=state_distribution,
        top_ads_by_spend=top_ads_by_spend,
    )


# ==========================================
# Эндпоинт — Таймлайн объявления
# ==========================================


@app.get("/api/ads/{fb_ad_id}/timeline")
async def get_ad_timeline(fb_ad_id: str, db: AsyncSession = Depends(get_db)):
    """Таймлайн событий по одному объявлению: алерты, метрики на каждый момент, динамика расхода."""
    # Текущий снэпшот
    snapshot_result = await db.execute(
        select(AdSnapshot).where(AdSnapshot.fb_ad_id == fb_ad_id)
    )
    snapshot = snapshot_result.scalar_one_or_none()

    # История алертов
    events_result = await db.execute(
        select(AlertEvent)
        .where(AlertEvent.fb_ad_id == fb_ad_id)
        .order_by(AlertEvent.created_at.desc())
    )
    events = events_result.scalars().all()

    # Задачи на отключение
    tasks_result = await db.execute(
        select(DisableTask)
        .where(DisableTask.fb_ad_id == fb_ad_id)
        .order_by(DisableTask.created_at.desc())
    )
    tasks = tasks_result.scalars().all()

    diagnostics = None
    if snapshot is not None:
        diagnostics_map = await _build_snapshot_diagnostics_map(db, [snapshot])
        diagnostics = diagnostics_map.get(snapshot.fb_ad_id)

    # Формируем таймлайн: объединяем алерты и задачи по времени
    timeline = []
    for e in events:
        m = e.metrics_json or {}
        timeline.append({
            "type": "alert",
            "time": e.created_at.isoformat(),
            "stage": e.stage.value if e.stage else None,
            "state": e.state.value if e.state else None,
            "matched_rules": e.matched_rule_codes or [],
            "reason_title": e.reason_title,
            "reason_text": e.reason_text,
            "spend": m.get("spend"),
            "clicks": m.get("clicks"),
            "cpc": m.get("cpc"),
            "outbound_clicks": m.get("outbound_clicks"),
            "outbound_ctr": m.get("outbound_ctr"),
            "landing_page_views": m.get("landing_page_views"),
            "cost_per_landing_page_view": m.get("cost_per_landing_page_view"),
            "cpm": m.get("cpm"),
            "frequency": m.get("frequency"),
            "leads": m.get("leads"),
            "registrations": m.get("registrations"),
            "deposits": m.get("deposits"),
        })
    for t in tasks:
        timeline.append({
            "type": "disable_task",
            "time": t.created_at.isoformat(),
            "status": t.status.value,
            "attempt_count": t.attempt_count,
            "requested_by": t.requested_by_username,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
            "last_error": t.last_error,
        })

    # Показываем новые события сверху, чтобы таймлайн читался как журнал.
    timeline.sort(key=lambda x: x["time"], reverse=True)

    return {
        "fb_ad_id": fb_ad_id,
        "ad_name": snapshot.ad_name if snapshot else None,
        "campaign_name": snapshot.campaign_name if snapshot else None,
        "adset_name": snapshot.adset_name if snapshot else None,
        "current_state": snapshot.alert_state.value if snapshot else None,
        "delivery_status": snapshot.delivery_status if snapshot else None,
        "current_metrics": {
            "spend": str(snapshot.spend) if snapshot else None,
            "clicks": snapshot.clicks if snapshot else None,
            "cpc": str(snapshot.cpc) if snapshot and snapshot.cpc is not None else None,
            "outbound_clicks": snapshot.outbound_clicks if snapshot else None,
            "outbound_ctr": str(snapshot.outbound_ctr) if snapshot and snapshot.outbound_ctr is not None else None,
            "landing_page_views": snapshot.landing_page_views if snapshot else None,
            "cost_per_landing_page_view": (
                str(snapshot.cost_per_landing_page_view)
                if snapshot and snapshot.cost_per_landing_page_view is not None
                else None
            ),
            "cpm": str(snapshot.cpm) if snapshot and snapshot.cpm is not None else None,
            "frequency": str(snapshot.frequency) if snapshot and snapshot.frequency is not None else None,
            "leads": snapshot.leads if snapshot else None,
            "cost_per_lead": str(snapshot.cost_per_lead) if snapshot and snapshot.cost_per_lead is not None else None,
            "registrations": snapshot.registrations if snapshot else None,
            "cost_per_registration": (
                str(snapshot.cost_per_registration)
                if snapshot and snapshot.cost_per_registration is not None
                else None
            ),
            "deposits": snapshot.deposits if snapshot else None,
        } if snapshot else None,
        "diagnostics": diagnostics.model_dump() if diagnostics else None,
        "last_observed_at": snapshot.last_observed_at.isoformat() if snapshot else None,
        "timeline": timeline,
    }


# ==========================================
# Эндпоинты — Vision настройки
# ==========================================


@app.get("/api/settings/vision", response_model=VisionSettingsSchema)
async def get_vision_settings(db: AsyncSession = Depends(get_db)):
    """Получить настройки Vision браузера (токен маскируется)."""
    result = await db.execute(
        select(VisionSettings).where(VisionSettings.singleton_key == "default")
    )
    row = result.scalar_one_or_none()
    if row is None:
        return VisionSettingsSchema()
    return VisionSettingsSchema(
        api_url=row.api_url,
        x_token="",  # Никогда не возвращаем расшифрованный токен
        profile_id=row.profile_id,
        has_token=bool(row.x_token_encrypted),
    )


@app.put("/api/settings/vision", response_model=VisionSettingsSchema)
async def update_vision_settings(
    body: VisionSettingsUpdateSchema, db: AsyncSession = Depends(get_db)
):
    """Обновить настройки Vision браузера."""
    result = await db.execute(
        select(VisionSettings).where(VisionSettings.singleton_key == "default")
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = VisionSettings(singleton_key="default")
        db.add(row)
    row.api_url = body.api_url
    if body.x_token:
        row.x_token_encrypted = encrypt(body.x_token)
    row.profile_id = body.profile_id
    await db.commit()
    return VisionSettingsSchema(
        api_url=row.api_url,
        x_token="",
        profile_id=row.profile_id,
        has_token=bool(row.x_token_encrypted),
    )


@app.post("/api/vision/reconnect")
async def vision_reconnect(db: AsyncSession = Depends(get_db)):
    """Запросить переподключение к Vision браузеру (флаг для observer).

    Observer подхватит флаг в следующем цикле сканирования и вызовет
    browser_manager.disconnect() + connect(). Требует, чтобы observer был запущен.
    """
    result = await db.execute(
        select(VisionSettings).where(VisionSettings.singleton_key == "default")
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = VisionSettings(singleton_key="default")
        db.add(row)
    row.reconnect_requested = True
    await db.commit()
    return {
        "ok": True,
        "message": (
            "Флаг переподключения установлен. "
            "Observer выполнит reconnect в следующем цикле сканирования."
        ),
    }


@app.get("/api/vision/profiles")
async def get_vision_profiles(db: AsyncSession = Depends(get_db)):
    """Получить список профилей Vision (проксируем запрос к Vision API)."""
    import httpx

    result = await db.execute(
        select(VisionSettings).where(VisionSettings.singleton_key == "default")
    )
    row = result.scalar_one_or_none()
    if row is None or not row.x_token_encrypted:
        raise HTTPException(status_code=400, detail="Vision X-Token не настроен")

    x_token = decrypt(row.x_token_encrypted)
    if not x_token:
        raise HTTPException(status_code=400, detail="Не удалось расшифровать Vision X-Token")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{row.api_url.rstrip('/')}/list",
                headers={"X-Token": x_token},
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Vision API вернул {resp.status_code}")
        data = resp.json()
        # Vision API возвращает {"profiles": [...]} (словарь, не список)
        raw = data.get("profiles") if isinstance(data, dict) else data
        profiles = []
        for item in raw if isinstance(raw, list) else []:
            profiles.append({
                "folder_id": item.get("folder_id", ""),
                "profile_id": item.get("profile_id", ""),
                "name": item.get("name") or item.get("profile_id", ""),
                "port": item.get("port"),
            })
        return profiles
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=502, detail=f"Не удалось подключиться к Vision API: {e}"
        ) from e


# ==========================================
# Эндпоинты — Telegram получатели (мультипользователи)
# ==========================================


@app.get("/api/settings/telegram/recipients", response_model=list[TelegramRecipientSchema])
async def list_telegram_recipients(db: AsyncSession = Depends(get_db)):
    """Список авторизованных получателей Telegram-уведомлений."""
    result = await db.execute(
        select(TelegramRecipient).order_by(TelegramRecipient.created_at.asc())
    )
    recipients = result.scalars().all()
    return [
        TelegramRecipientSchema(
            id=str(r.id),
            chat_id=r.chat_id,
            username=r.username,
            first_name=r.first_name,
            is_active=r.is_active,
            created_at=r.created_at.isoformat(),
        )
        for r in recipients
    ]


@app.delete("/api/settings/telegram/recipients/{recipient_id}")
async def delete_telegram_recipient(
    recipient_id: str, db: AsyncSession = Depends(get_db)
):
    """Удалить получателя Telegram-уведомлений."""
    result = await db.execute(
        select(TelegramRecipient).where(TelegramRecipient.id == _uuid.UUID(recipient_id))
    )
    recipient = result.scalar_one_or_none()
    if recipient is None:
        raise HTTPException(status_code=404, detail="Получатель не найден")
    await db.delete(recipient)
    await db.commit()
    return {"ok": True}


@app.post("/api/settings/telegram/recipients/invite", response_model=InviteCodeResponse)
async def create_invite_code(db: AsyncSession = Depends(get_db)):
    """Сгенерировать одноразовый код для добавления нового получателя."""
    result = await db.execute(
        select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
    )
    row = result.scalar_one_or_none()
    if row is None or not row.is_authorized:
        raise HTTPException(status_code=400, detail="Telegram-бот не настроен")

    # Генерируем 6-значный код
    code = str(secrets.randbelow(900000) + 100000)

    # Добавляем в список pending_codes
    current_codes = list(row.pending_codes or [])
    current_codes.append(code)
    row.pending_codes = current_codes
    await db.commit()

    return InviteCodeResponse(code=code, bot_username=row.bot_username)

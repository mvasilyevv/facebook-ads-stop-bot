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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.routes.miniapp import router as miniapp_router
from core.browser.vision_client import VisionClient
from core.config import get_settings
from core.crypto import decrypt, encrypt
from core.db import get_engine, get_session_factory
from core.db.base import Base
from core.diagnostics import build_ad_quality_diagnostics, compute_cpm_baselines_by_offer
from core.disable_tasks import (
    DISABLE_TASK_STALE_TIMEOUT,
    SILENT_DISABLE_INCIDENT_RETRY_LIMIT,
    is_delivery_disabled,
)
from core.domain import (
    AlertStage,
    AlertState,
    DisableTaskStatus,
    EnableRecommendationLevel,
    EnableTaskStatus,
    TelegramDeliveryMode,
    TelegramUserRole,
)
from core.enable_recommendations.service import (
    OK_RECOMMENDATION_REASON_TEXT,
    OK_RECOMMENDATION_REASON_TITLE,
    RECOMMENDATION_DELIVERY_STATUSES,
    EnableRecommendationCandidate,
    collect_enable_recommendation_candidates_for_snapshots,
    promote_recommendation_to_enable_task,
)
from core.live_batch import compute_live_batch_marker, is_within_live_batch, load_live_batch_bounds
from core.models import (
    AdSnapshot,
    AlertEvent,
    CabinetDayArchive,
    DisableTask,
    EnableRecommendationEvent,
    EnableTask,
    ObserverSettings,
    Offer,
    OfferRuleConfig,
    TelegramInvite,
    TelegramRecipient,
    TelegramSettings,
    VisionSettings,
)
from core.observer.thresholds import (
    apply_observer_threshold_values,
    derive_legacy_stop_percent_of_base,
    derive_legacy_warning_percent_of_stop,
    extract_observer_threshold_values,
)
from core.telegram.client import TelegramBotClient
from core.telegram.service import (
    CONTROL_TOPIC_NAME,
    FORUM_STREAM_TOPIC_NAMES,
    FORUM_SUPERGROUP_CHAT_ID,
    build_telegram_deep_link,
    create_telegram_invite,
    forum_cutover_status_from_settings,
    forum_topics_ready,
    get_latest_active_invite,
    get_or_create_telegram_settings,
    is_forum_delivery_mode,
    mask_chat_id,
    poller_status_from_settings,
    revoke_telegram_access_records,
)

# ==========================================
# Lifespan — инициализация БД
# ==========================================


def _has_alembic_migrations() -> bool:
    """Проверяет, есть ли в проекте реальные Alembic-миграции."""
    versions_dir = Path(__file__).resolve().parents[2] / "migrations" / "versions"
    if not versions_dir.exists():
        return False
    return any(
        path.suffix == ".py" and path.name != "__init__.py" for path in versions_dir.iterdir()
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Создаём таблицы только когда проект работает без Alembic-миграций."""
    engine = get_engine()
    if not _has_alembic_migrations():
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

# Включаем маршруты MiniApp
app.include_router(miniapp_router)


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


def _normalize_offer_code_value(value: str | None) -> str | None:
    """Приводит код оффера к каноническому виду для UI и API."""
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return ""
    return normalized.upper()


def _offer_code_lookup_key(value: str | None) -> str:
    """Приводит код оффера к виду для case-insensitive поиска."""
    normalized = _normalize_offer_code_value(value)
    return normalized.casefold() if normalized else ""


def _build_current_risk_reason_rows(snapshots: list[AdSnapshot]) -> list[dict[str, int | str]]:
    """Строит топ причин по текущим рискованным объявлениям."""
    risk_labels = {
        "cpc_stop": ("Дорогой клик", "Дорогой клик"),
        "cpl_stop": ("Дорогой лид", "Дорогой лид"),
        "cpr_stop": ("Дорогая рега", "Дорогая рега"),
        "regs_no_dep_stop": ("Реги без депозитов", "Реги без депов"),
        "spend_no_dep_range": ("Расход без депа", "Расход без депа"),
        "spend_with_dep_range": ("Расход с депозитом", "Расход с депозитом"),
        "early_outbound_ctr_signal": ("Слабый CTR исходящих кликов", "Слабый CTR"),
        "early_lpv_ratio_signal": ("Слабая доходимость до лендинга", "Слабая доходимость"),
        "early_cost_per_lpv_signal": ("Дорогой просмотр лендинга", "Дорогой LPV"),
    }
    risk_counts: dict[str, int] = {}
    for snapshot in snapshots:
        if snapshot.alert_state == AlertState.EARLY_SIGNAL_SENT:
            matched_codes = snapshot.early_signal_rule_codes or []
        elif snapshot.alert_state == AlertState.WARNING_SENT:
            matched_codes = snapshot.warning_rule_codes or []
        elif snapshot.alert_state in (AlertState.STOP_SENT, AlertState.CLAIMED):
            matched_codes = snapshot.stop_rule_codes or []
        else:
            matched_codes = []

        for code in set(matched_codes):
            risk_counts[code] = risk_counts.get(code, 0) + 1

    return sorted(
        [
            {
                "rule": risk_labels.get(code, (code, code))[0],
                "rule_short": risk_labels.get(code, (code, code))[1],
                "count": count,
            }
            for code, count in risk_counts.items()
        ],
        key=lambda item: (-int(item["count"]), str(item["rule"])),
    )


class ObserverSettingsSchema(BaseModel):
    """Настройки observer (интервал из UI)."""

    interval_seconds: int = 90
    jitter_seconds: int = 10
    warning_percent_of_stop: Decimal = Decimal("80")
    stop_percent_of_base: Decimal = Decimal("100")
    cpc_warning_percent_of_stop: Decimal | None = None
    cpc_stop_percent_of_base: Decimal | None = None
    cpl_warning_percent_of_stop: Decimal | None = None
    cpl_stop_percent_of_base: Decimal | None = None
    cpr_warning_percent_of_stop: Decimal | None = None
    cpr_stop_percent_of_base: Decimal | None = None
    is_scanning_enabled: bool = True


class ScanningToggleSchema(BaseModel):
    """Схема для быстрого переключения сканирования."""

    enabled: bool


class TelegramSettingsSchema(BaseModel):
    """Настройки Telegram-бота."""

    bot_token: str = ""
    chat_id: str = ""
    forum_chat_id: str = ""
    is_authorized: bool = False
    bot_username: str = ""
    auth_code: str = ""
    delivery_mode: str = TelegramDeliveryMode.PRIVATE_CHAT.value
    control_topic_id: int | None = None
    early_topic_id: int | None = None
    warning_topic_id: int | None = None
    stop_topic_id: int | None = None
    enable_topic_id: int | None = None


class TelegramPrimaryRecipientSchema(BaseModel):
    """Основной получатель уведомлений Telegram."""

    chat_id: str = ""
    masked_chat_id: str = ""
    telegram_user_id: str = ""
    username: str = ""
    first_name: str = ""
    role: str = TelegramUserRole.OWNER.value


class InviteCodeResponse(BaseModel):
    """Ответ с одноразовым кодом для добавления получателя."""

    code: str
    bot_username: str = ""
    role: str = TelegramUserRole.RECIPIENT.value
    expires_at: str | None = None
    deep_link: str = ""
    activation_command: str = ""
    activation_target: str = CONTROL_TOPIC_NAME


class TelegramSettingsResponseSchema(TelegramSettingsSchema):
    """Расширенные настройки Telegram-бота."""

    poller_status: str = "OFFLINE"
    last_poller_heartbeat_at: str | None = None
    auth_deep_link: str = ""
    activation_command: str = ""
    forum_cutover_status: str = "NOT_STARTED"
    primary_recipient: TelegramPrimaryRecipientSchema | None = None
    active_invite: InviteCodeResponse | None = None


class TelegramForumCutoverResponseSchema(BaseModel):
    """Ответ на подготовку cutover в forum supergroup."""

    bot_username: str = ""
    chat_id: str = ""
    auth_code: str = ""
    activation_command: str = ""
    control_topic_id: int | None = None
    early_topic_id: int | None = None
    warning_topic_id: int | None = None
    stop_topic_id: int | None = None
    enable_topic_id: int | None = None
    forum_cutover_status: str = "WAITING_OWNER_AUTH"
    message: str = ""


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

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        """Нормализует код оффера в верхний регистр."""
        normalized = _normalize_offer_code_value(value)
        return normalized or ""


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
    budget: str = ""
    reach: int = 0
    impressions: int = 0
    clicks: int
    cpc: Decimal | None = None
    ctr: Decimal | None = None
    outbound_clicks: int = 0
    outbound_ctr: Decimal | None = None
    landing_page_views: int = 0
    cost_per_landing_page_view: Decimal | None = None
    cost_per_result: Decimal | None = None
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

    @field_validator("offer_code")
    @classmethod
    def normalize_offer_code(cls, value: str | None) -> str | None:
        """Нормализует код оффера в ответе API."""
        return _normalize_offer_code_value(value)


class AlertEventSchema(BaseModel):
    """Запись алерта для истории."""

    id: str
    incident_key: str | None = None
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
    incident_key: str
    fb_ad_id: str
    ad_name: str
    status: str
    attempt_count: int
    last_error: str | None = None
    next_retry_at: str | None = None
    requested_by_username: str | None = None
    created_at: str
    updated_at: str
    completed_at: str | None = None


class CreateDisableTaskRequest(BaseModel):
    """Запрос на создание задачи отключения."""

    fb_ad_id: str


class ActiveIncidentSchema(BaseModel):
    """Текущий открытый инцидент объявления."""

    incident_key: str
    fb_ad_id: str
    ad_name: str
    campaign_name: str
    adset_name: str
    current_state: str
    current_stage: str | None = None
    delivery_status: str
    matched_rule_codes: list[str] = []
    reason_title: str | None = None
    reason_text: str | None = None
    metrics_json: dict = {}
    started_at: str | None = None
    last_activity_at: str
    last_observed_at: str | None = None
    latest_alert_at: str | None = None
    latest_alert_stage: str | None = None
    latest_disable_task_status: str | None = None
    latest_disable_task_created_at: str | None = None
    latest_disable_task_updated_at: str | None = None
    latest_disable_task_attempt: int | None = None
    latest_disable_task_id: str | None = None
    latest_disable_task_last_error: str | None = None
    latest_disable_task_next_retry_at: str | None = None
    latest_disable_task_completed_at: str | None = None
    waiting_for_off: bool = False
    has_active_disable_task: bool = False
    incident_retry_count: int = 0
    needs_manual_attention: bool = False


class EnableRecommendationEventSchema(BaseModel):
    """Событие рекомендации на включение для dashboard."""

    id: str
    fb_ad_id: str
    ad_name: str
    campaign_name: str | None = None
    adset_name: str | None = None
    delivery_status: str
    recommendation_level: str
    matched_rule_codes: list[str] = []
    reason_title: str | None = None
    reason_text: str | None = None
    metrics_json: dict = {}
    live_batch_started_at: str
    created_at: str
    updated_at: str | None = None
    state: str = "OPEN"
    related_enable_task_id: str | None = None
    related_enable_task_status: str | None = None


@dataclass(slots=True, frozen=True)
class CurrentEnableRecommendationRow:
    """Текущая live-проекция рекомендации для dashboard."""

    event: EnableRecommendationEvent
    snapshot: AdSnapshot
    candidate: EnableRecommendationCandidate


NEUTRAL_ENABLE_RECOMMENDATION_REASON_TITLE = "Нет блокирующих сигналов"
NEUTRAL_ENABLE_RECOMMENDATION_REASON_TEXT = "По текущим правилам блокирующих сигналов нет."
GENERIC_ENABLE_RECOMMENDATION_REASON_TITLES = {
    OK_RECOMMENDATION_REASON_TITLE,
    "Метрики в норме",
}
GENERIC_ENABLE_RECOMMENDATION_REASON_TEXTS = {
    OK_RECOMMENDATION_REASON_TEXT,
    "Объявление снова проходит по текущим правилам.",
}


def _normalize_enable_recommendation_reason(
    *,
    recommendation_level: EnableRecommendationLevel,
    reason_title: str | None,
    reason_text: str | None,
) -> tuple[str | None, str | None]:
    """Убирает позитивный дефолт у generic OK-рекомендаций."""
    if str(recommendation_level).upper() != "OK":
        return reason_title, reason_text

    normalized_title = reason_title
    normalized_text = reason_text
    if normalized_title is None or normalized_title in GENERIC_ENABLE_RECOMMENDATION_REASON_TITLES:
        normalized_title = NEUTRAL_ENABLE_RECOMMENDATION_REASON_TITLE
    if normalized_text is None or normalized_text in GENERIC_ENABLE_RECOMMENDATION_REASON_TEXTS:
        normalized_text = NEUTRAL_ENABLE_RECOMMENDATION_REASON_TEXT
    return normalized_title, normalized_text


class EnableTaskSchema(BaseModel):
    """Задача на включение для мониторинга."""

    id: str
    recommendation_event_id: str | None = None
    fb_ad_id: str
    ad_name: str
    status: str
    attempt_count: int
    last_error: str | None = None
    next_retry_at: str | None = None
    requested_by_username: str | None = None
    created_at: str
    updated_at: str | None = None
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
    ads_disabled_today: int = 0  # успешно отключено ботом в текущем окне мониторинга
    total_spend: Decimal = Decimal("0")
    active_offers: int = 0
    pending_disable_tasks: int = 0
    pending_enable_tasks: int = 0
    enable_recommendations_ok: int = 0
    enable_recommendations_early_signal: int = 0
    enable_recommendations_warning: int = 0
    last_scan_at: str | None = None
    observer_status: str | None = None
    observer_status_message: str | None = None
    observer_heartbeat_at: str | None = None
    observer_last_error: str | None = None
    observer_last_error_at: str | None = None


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


class DashboardBatchSchema(BaseModel):
    """Батч-ответ для одного запроса вместо 4."""

    ads: list[AdSnapshotSchema] = []
    stats: DashboardStatsSchema | None = None
    incidents: list[ActiveIncidentSchema] = []
    disable_tasks: list[DisableTaskSchema] = []


class ChartDataSchema(BaseModel):
    """Данные для графиков на главной странице."""

    alerts_by_hour: list[dict] = []
    rule_violations: list[dict] = []
    campaigns: list[dict] = []
    state_distribution: list[dict] = []
    top_ads_by_spend: list[dict] = []
    campaign_budget_deltas: list[dict] = []
    campaign_stop_overruns: list[dict] = []


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
    masked_chat_id: str = ""
    telegram_user_id: str = ""
    username: str = ""
    first_name: str = ""
    role: str = TelegramUserRole.RECIPIENT.value
    is_active: bool = True
    created_at: str


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
    threshold_values = extract_observer_threshold_values(row)
    if row is None:
        return ObserverSettingsSchema(**threshold_values)
    return ObserverSettingsSchema(
        interval_seconds=row.interval_seconds,
        jitter_seconds=row.jitter_seconds,
        **threshold_values,
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
    threshold_values = extract_observer_threshold_values(body)
    if "warning_percent_of_stop" not in body.model_fields_set:
        threshold_values["warning_percent_of_stop"] = derive_legacy_warning_percent_of_stop(
            threshold_values
        )
    if "stop_percent_of_base" not in body.model_fields_set:
        threshold_values["stop_percent_of_base"] = derive_legacy_stop_percent_of_base(
            threshold_values
        )
    row.interval_seconds = body.interval_seconds
    row.jitter_seconds = body.jitter_seconds
    apply_observer_threshold_values(row, threshold_values)
    row.is_scanning_enabled = body.is_scanning_enabled
    await db.commit()
    return ObserverSettingsSchema(
        interval_seconds=row.interval_seconds,
        jitter_seconds=row.jitter_seconds,
        **extract_observer_threshold_values(row),
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


def _observer_runtime_paths() -> tuple[Path, Path, Path, str]:
    """Возвращает пути и python-бинарь для управления observer worker."""
    project_root = Path(__file__).parent.parent.parent
    pid_file = project_root / ".logs" / "pids.txt"
    log_file = project_root / ".logs" / "observer.log"
    run_script = project_root / "run_observer.py"
    venv_python = project_root / ".venv" / "bin" / "python"
    python_bin = str(venv_python) if venv_python.exists() else sys.executable
    return pid_file, log_file, run_script, python_bin


def _disable_runtime_paths() -> tuple[Path, Path, Path, str]:
    """Возвращает пути и python-бинарь для управления воркером отключения."""
    project_root = Path(__file__).parent.parent.parent
    pid_file = project_root / ".logs" / "pids.txt"
    log_file = project_root / ".logs" / "disable_worker.log"
    run_script = project_root / "run_disable_worker.py"
    venv_python = project_root / ".venv" / "bin" / "python"
    python_bin = str(venv_python) if venv_python.exists() else sys.executable
    return pid_file, log_file, run_script, python_bin


def _read_lines_from_file(path: Path) -> list[str]:
    """Читает файл построчно или возвращает пустой список, если файла нет."""
    if not path.exists():
        return []
    return path.read_text().splitlines()


def _write_pid_lines(path: Path, lines: list[str]) -> None:
    """Перезаписывает PID-файл подготовленным списком строк."""
    path.write_text("\n".join(lines) + "\n" if lines else "")


def _read_pid_from_file(path: Path) -> int | None:
    """Читает PID из файла-одиночки."""
    if not path.exists():
        return None
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
        return None


def _unlink_file(path: Path) -> None:
    """Удаляет файл, если он существует."""
    path.unlink(missing_ok=True)


def _append_text(path: Path, text: str) -> None:
    """Дописвает строку в конец файла."""
    with path.open("a") as handle:
        handle.write(text)


def _open_append_handle(path: Path):
    """Открывает файловый дескриптор в режиме append."""
    return path.open("a")


async def _stop_observer_process() -> int | None:
    """Останавливает текущий observer worker и удаляет его PID из файла."""
    pid_file, _, _, _ = _observer_runtime_paths()

    # Находим и завершаем текущий процесс воркера
    old_pid: int | None = None
    lines = await asyncio.to_thread(_read_lines_from_file, pid_file)
    if lines:
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
            await asyncio.to_thread(_write_pid_lines, pid_file, remaining)
    return old_pid


async def _stop_disable_process() -> int | None:
    """Останавливает текущий воркер отключения и удаляет его PID из файла."""
    pid_file, _, _, _ = _disable_runtime_paths()
    singleton_pid_file = Path("/tmp/fb_disable_worker.pid")

    old_pid = await asyncio.to_thread(_read_pid_from_file, singleton_pid_file)

    lines = await asyncio.to_thread(_read_lines_from_file, pid_file)
    if lines:
        remaining = []
        for line in lines:
            parts = line.strip().split()
            if len(parts) == 2 and parts[1] == "disable_worker":
                if old_pid is None:
                    try:
                        old_pid = int(parts[0])
                    except ValueError:
                        pass
            else:
                remaining.append(line)
        await asyncio.to_thread(_write_pid_lines, pid_file, remaining)

    if old_pid:
        try:
            os.kill(old_pid, signal.SIGTERM)
            await asyncio.sleep(2.0)
            try:
                os.kill(old_pid, 0)
                os.kill(old_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        except ProcessLookupError:
            pass

    await asyncio.to_thread(_unlink_file, singleton_pid_file)
    return old_pid


async def _start_observer_process(*, reason: str) -> int:
    """Запускает observer worker и сохраняет его PID в файл."""
    pid_file, log_file, run_script, python_bin = _observer_runtime_paths()

    await asyncio.to_thread(
        _append_text,
        log_file,
        f"\n--- {reason} {datetime.now(UTC).isoformat()} ---\n",
    )
    stdout_handle = await asyncio.to_thread(_open_append_handle, log_file)

    try:
        proc = await asyncio.create_subprocess_exec(
            python_bin,
            str(run_script),
            stdout=stdout_handle,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(run_script.parent),
        )
    finally:
        stdout_handle.close()

    # Сохраняем новый PID
    await asyncio.to_thread(_append_text, pid_file, f"{proc.pid} observer\n")

    return proc.pid


async def _start_disable_process(*, reason: str) -> int:
    """Запускает воркер отключения и сохраняет его PID в файл."""
    pid_file, log_file, run_script, python_bin = _disable_runtime_paths()

    await asyncio.to_thread(
        _append_text,
        log_file,
        f"\n--- {reason} {datetime.now(UTC).isoformat()} ---\n",
    )
    stdout_handle = await asyncio.to_thread(_open_append_handle, log_file)

    try:
        proc = await asyncio.create_subprocess_exec(
            python_bin,
            str(run_script),
            stdout=stdout_handle,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(run_script.parent),
        )
    finally:
        stdout_handle.close()

    await asyncio.to_thread(_append_text, pid_file, f"{proc.pid} disable_worker\n")

    return proc.pid


@app.post("/api/observer/restart")
async def restart_observer():
    """Перезапуск observer worker: завершает текущий процесс и запускает новый."""
    old_pid = await _stop_observer_process()
    new_pid = await _start_observer_process(reason="Перезапуск воркера через UI")

    return {"restarted": True, "old_pid": old_pid, "new_pid": new_pid}


@app.post("/api/disable-worker/restart")
async def restart_disable_worker():
    """Перезапуск воркера отключения: завершает зависший процесс и поднимает новый."""
    old_pid = await _stop_disable_process()
    new_pid = await _start_disable_process(reason="Перезапуск воркера отключения через интерфейс")

    return {"restarted": True, "old_pid": old_pid, "new_pid": new_pid}


def _mask_bot_token(token: str) -> str:
    """Маскирует bot token для безопасного отображения."""
    return (token[:10] + "***") if len(token) > 10 else ("***" if token else "")


def _serialize_primary_recipient(
    row: TelegramSettings | None,
) -> TelegramPrimaryRecipientSchema | None:
    """Собирает primary recipient из telegram_settings."""
    if row is None or not row.chat_id:
        return None
    return TelegramPrimaryRecipientSchema(
        chat_id=row.chat_id,
        masked_chat_id=mask_chat_id(row.chat_id),
        telegram_user_id=row.owner_telegram_user_id or "",
        username=row.owner_username or "",
        first_name=row.owner_first_name or "",
        role=TelegramUserRole.OWNER.value,
    )


def _activation_command(code: str) -> str:
    """Строит текст команды активации для Telegram."""
    return f"/start {code}".strip() if code else ""


def _serialize_invite_response(
    invite: TelegramInvite | None,
    *,
    bot_username: str,
    delivery_mode: str = TelegramDeliveryMode.PRIVATE_CHAT.value,
) -> InviteCodeResponse | None:
    """Сериализует активный инвайт для UI."""
    if invite is None:
        return None
    activation_command = _activation_command(invite.code)
    return InviteCodeResponse(
        code=invite.code,
        bot_username=bot_username or "",
        role=invite.role or TelegramUserRole.RECIPIENT.value,
        expires_at=invite.expires_at.isoformat() if invite.expires_at else None,
        deep_link=(
            ""
            if str(delivery_mode).upper() == TelegramDeliveryMode.FORUM_GROUP.value
            else build_telegram_deep_link(bot_username or "", invite.code)
        ),
        activation_command=activation_command,
        activation_target=CONTROL_TOPIC_NAME,
    )


async def _create_forum_topics_if_needed(
    *,
    bot_token: str,
    settings_row: TelegramSettings,
) -> dict[str, int]:
    """Создаёт production topics для forum supergroup или переиспользует уже сохранённые."""
    if settings_row.chat_id == FORUM_SUPERGROUP_CHAT_ID and forum_topics_ready(settings_row):
        return {
            "control_topic_id": int(settings_row.control_topic_id or 0),
            "early_topic_id": int(settings_row.early_topic_id or 0),
            "warning_topic_id": int(settings_row.warning_topic_id or 0),
            "stop_topic_id": int(settings_row.stop_topic_id or 0),
            "enable_topic_id": int(settings_row.enable_topic_id or 0),
        }

    client = TelegramBotClient(bot_token)
    try:
        chat = await client.get_chat(chat_id=FORUM_SUPERGROUP_CHAT_ID)
        if str(chat.get("type") or "") != "supergroup":
            raise HTTPException(
                status_code=400,
                detail="Telegram-группа для cutover должна быть supergroup.",
            )
        if not bool(chat.get("is_forum")):
            raise HTTPException(
                status_code=400,
                detail="В Telegram-группе не включён режим forum topics.",
            )

        created_topics: dict[str, int] = {}
        for stream_key, title in FORUM_STREAM_TOPIC_NAMES.items():
            topic = await client.create_forum_topic(
                chat_id=FORUM_SUPERGROUP_CHAT_ID,
                name=title,
            )
            topic_id = int(topic["message_thread_id"])
            if stream_key == "CONTROL":
                created_topics["control_topic_id"] = topic_id
            elif stream_key == "EARLY":
                created_topics["early_topic_id"] = topic_id
            elif stream_key == "WARNING":
                created_topics["warning_topic_id"] = topic_id
            elif stream_key == "STOP":
                created_topics["stop_topic_id"] = topic_id
            elif stream_key == "ENABLE":
                created_topics["enable_topic_id"] = topic_id
        return created_topics
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Не удалось подготовить forum topics. Проверьте права бота в группе.",
        ) from exc
    finally:
        await client.close()


async def _prepare_telegram_forum_cutover(
    *,
    db: AsyncSession,
    settings_row: TelegramSettings,
    bot_token: str,
    bot_username: str,
) -> TelegramForumCutoverResponseSchema:
    """Готовит Telegram-контур к переезду в forum supergroup."""
    topic_ids = await _create_forum_topics_if_needed(
        bot_token=bot_token,
        settings_row=settings_row,
    )
    auth_code = str(secrets.randbelow(900000) + 100000)
    await revoke_telegram_access_records(db)

    settings_row.delivery_mode = TelegramDeliveryMode.FORUM_GROUP
    settings_row.chat_id = FORUM_SUPERGROUP_CHAT_ID
    settings_row.is_authorized = False
    settings_row.auth_code = auth_code
    settings_row.owner_telegram_user_id = ""
    settings_row.owner_username = ""
    settings_row.owner_first_name = ""
    settings_row.bot_username = bot_username or settings_row.bot_username or ""
    settings_row.control_topic_id = topic_ids["control_topic_id"]
    settings_row.early_topic_id = topic_ids["early_topic_id"]
    settings_row.warning_topic_id = topic_ids["warning_topic_id"]
    settings_row.stop_topic_id = topic_ids["stop_topic_id"]
    settings_row.enable_topic_id = topic_ids["enable_topic_id"]
    await db.commit()

    return TelegramForumCutoverResponseSchema(
        bot_username=settings_row.bot_username or "",
        chat_id=FORUM_SUPERGROUP_CHAT_ID,
        auth_code=auth_code,
        activation_command=_activation_command(auth_code),
        control_topic_id=settings_row.control_topic_id,
        early_topic_id=settings_row.early_topic_id,
        warning_topic_id=settings_row.warning_topic_id,
        stop_topic_id=settings_row.stop_topic_id,
        enable_topic_id=settings_row.enable_topic_id,
        forum_cutover_status=forum_cutover_status_from_settings(settings_row),
        message=(
            "Forum topics готовы. Откройте topic CONTROL в группе AdGuard FB Bot "
            "и отправьте команду активации."
        ),
    )


@app.get("/api/settings/telegram", response_model=TelegramSettingsResponseSchema)
async def get_telegram_settings(db: AsyncSession = Depends(get_db)):
    """Получить настройки Telegram (токен маскируется)."""
    row = await db.scalar(
        select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
    )
    if row is None:
        s = get_settings()
        primary_recipient = None
        if s.telegram_chat_id:
            primary_recipient = TelegramPrimaryRecipientSchema(
                chat_id=s.telegram_chat_id,
                masked_chat_id=mask_chat_id(s.telegram_chat_id),
                role=TelegramUserRole.OWNER.value,
            )
        return TelegramSettingsResponseSchema(
            bot_token=_mask_bot_token(s.telegram_bot_token),
            chat_id=s.telegram_chat_id,
            forum_chat_id="",
            is_authorized=bool(s.telegram_chat_id),
            delivery_mode=TelegramDeliveryMode.PRIVATE_CHAT.value,
            poller_status="OFFLINE",
            forum_cutover_status="NOT_CONFIGURED",
            activation_command="",
            primary_recipient=primary_recipient,
        )

    token = decrypt(row.bot_token_encrypted) if row.bot_token_encrypted else ""
    active_invite = await get_latest_active_invite(db)
    delivery_mode = str(getattr(row, "delivery_mode", TelegramDeliveryMode.PRIVATE_CHAT.value))
    auth_code = row.auth_code if not row.is_authorized else ""
    auth_deep_link = ""
    if delivery_mode != TelegramDeliveryMode.FORUM_GROUP.value:
        auth_deep_link = build_telegram_deep_link(row.bot_username or "", auth_code or "")
    return TelegramSettingsResponseSchema(
        bot_token=_mask_bot_token(token),
        chat_id=row.chat_id,
        forum_chat_id=row.chat_id
        if delivery_mode == TelegramDeliveryMode.FORUM_GROUP.value
        else "",
        is_authorized=row.is_authorized,
        bot_username=row.bot_username,
        auth_code=auth_code,
        delivery_mode=delivery_mode,
        control_topic_id=row.control_topic_id,
        early_topic_id=row.early_topic_id,
        warning_topic_id=row.warning_topic_id,
        stop_topic_id=row.stop_topic_id,
        enable_topic_id=row.enable_topic_id,
        poller_status=poller_status_from_settings(row),
        last_poller_heartbeat_at=(
            row.poller_heartbeat_at.isoformat() if row.poller_heartbeat_at else None
        ),
        auth_deep_link=auth_deep_link,
        activation_command=_activation_command(auth_code),
        forum_cutover_status=forum_cutover_status_from_settings(row),
        primary_recipient=_serialize_primary_recipient(row),
        active_invite=_serialize_invite_response(
            active_invite,
            bot_username=row.bot_username or "",
            delivery_mode=delivery_mode,
        ),
    )


@app.put("/api/settings/telegram/token")
async def set_telegram_token(body: TelegramSetTokenRequest, db: AsyncSession = Depends(get_db)):
    """Установить bot_token и сразу подготовить forum-cutover."""
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

    row = await get_or_create_telegram_settings(db)
    row.bot_token_encrypted = encrypt(token)
    row.bot_username = bot_username
    await db.flush()

    cutover = await _prepare_telegram_forum_cutover(
        db=db,
        settings_row=row,
        bot_token=token,
        bot_username=bot_username,
    )
    return {
        "bot_username": cutover.bot_username,
        "auth_code": cutover.auth_code,
        "auth_deep_link": "",
        "activation_command": cutover.activation_command,
        "control_topic_id": cutover.control_topic_id,
        "forum_cutover_status": cutover.forum_cutover_status,
        "message": cutover.message,
    }


@app.post(
    "/api/settings/telegram/forum/cutover",
    response_model=TelegramForumCutoverResponseSchema,
)
async def prepare_telegram_forum_cutover(db: AsyncSession = Depends(get_db)):
    """Готовит Telegram-контур к переезду в forum supergroup без смены токена."""
    row = await db.scalar(
        select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
    )
    if row is None or not row.bot_token_encrypted:
        raise HTTPException(status_code=400, detail="Telegram-бот не настроен")

    token = decrypt(row.bot_token_encrypted)
    if not token:
        raise HTTPException(status_code=400, detail="Не удалось прочитать токен Telegram-бота")

    return await _prepare_telegram_forum_cutover(
        db=db,
        settings_row=row,
        bot_token=token,
        bot_username=row.bot_username or "",
    )


@app.delete("/api/settings/telegram")
async def revoke_telegram(db: AsyncSession = Depends(get_db)):
    """Отозвать авторизацию Telegram — сбрасывает все настройки."""
    row = await db.scalar(
        select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
    )
    if row is not None:
        await revoke_telegram_access_records(db)
        row.bot_token_encrypted = ""
        row.chat_id = ""
        row.is_authorized = False
        row.auth_code = ""
        row.bot_username = ""
        row.owner_telegram_user_id = ""
        row.owner_username = ""
        row.owner_first_name = ""
        row.delivery_mode = TelegramDeliveryMode.PRIVATE_CHAT
        row.control_topic_id = None
        row.early_topic_id = None
        row.warning_topic_id = None
        row.stop_topic_id = None
        row.enable_topic_id = None
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


def _serialize_optional_datetime(value: datetime | None) -> str | None:
    """Сериализует дату в ISO-формат."""
    return value.isoformat() if value else None


def _serialize_optional_decimal(value: object | None, precision: int) -> str | None:
    """Сериализует Decimal-подобное значение в строку нужной точности."""
    if value is None:
        return None
    return f"{Decimal(str(value)):.{precision}f}"


def _build_snapshot_metrics_json(snapshot: AdSnapshot | None) -> dict[str, object]:
    """Собирает текущие метрики рекомендации из актуального snapshot."""
    if snapshot is None:
        return {}
    return {
        "spend": _serialize_optional_decimal(getattr(snapshot, "spend", None), 2) or "0.00",
        "budget": str(getattr(snapshot, "budget", "") or "").strip() or None,
        "reach": int(getattr(snapshot, "reach", 0) or 0),
        "impressions": int(getattr(snapshot, "impressions", 0) or 0),
        "clicks": int(getattr(snapshot, "clicks", 0) or 0),
        "cpc": _serialize_optional_decimal(getattr(snapshot, "cpc", None), 4),
        "ctr": _serialize_optional_decimal(getattr(snapshot, "ctr", None), 4),
        "outbound_clicks": int(getattr(snapshot, "outbound_clicks", 0) or 0),
        "outbound_ctr": _serialize_optional_decimal(getattr(snapshot, "outbound_ctr", None), 4),
        "landing_page_views": int(getattr(snapshot, "landing_page_views", 0) or 0),
        "cost_per_result": _serialize_optional_decimal(
            getattr(snapshot, "cost_per_result", None), 4
        ),
        "cost_per_landing_page_view": _serialize_optional_decimal(
            getattr(snapshot, "cost_per_landing_page_view", None),
            4,
        ),
        "cpm": _serialize_optional_decimal(getattr(snapshot, "cpm", None), 4),
        "frequency": _serialize_optional_decimal(getattr(snapshot, "frequency", None), 4),
        "leads": int(getattr(snapshot, "leads", 0) or 0),
        "cost_per_lead": _serialize_optional_decimal(getattr(snapshot, "cost_per_lead", None), 4),
        "registrations": int(getattr(snapshot, "registrations", 0) or 0),
        "cost_per_registration": _serialize_optional_decimal(
            getattr(snapshot, "cost_per_registration", None),
            4,
        ),
        "deposits": int(getattr(snapshot, "deposits", 0) or 0),
    }


def _incident_key_for_snapshot(snapshot: AdSnapshot) -> str:
    """Возвращает ключ текущего инцидента для snapshot."""
    return snapshot.open_state_token or snapshot.telegram_group_key or snapshot.fb_ad_id


def _matched_rule_codes_for_snapshot(snapshot: AdSnapshot) -> list[str]:
    """Возвращает набор правил для текущей стадии snapshot."""
    if snapshot.current_stage == AlertStage.EARLY_SIGNAL:
        return list(snapshot.early_signal_rule_codes or [])
    if snapshot.current_stage == AlertStage.WARNING:
        return list(snapshot.warning_rule_codes or [])
    return list(snapshot.stop_rule_codes or [])


def _disable_task_activity_at(task: DisableTask) -> datetime:
    """Возвращает момент последней активности disable-задачи."""
    return task.updated_at or task.completed_at or task.created_at


def _max_datetime(*values: datetime | None) -> datetime | None:
    """Возвращает максимальную непустую дату."""
    filtered = [value for value in values if value is not None]
    if not filtered:
        return None
    return max(filtered)


def _min_datetime(*values: datetime | None) -> datetime | None:
    """Возвращает минимальную непустую дату."""
    filtered = [value for value in values if value is not None]
    if not filtered:
        return None
    return min(filtered)


def _serialize_disable_task(task: DisableTask) -> DisableTaskSchema:
    """Сериализует DisableTask для API-ответа."""
    incident_key = getattr(task, "open_state_token", "") or getattr(task, "fb_ad_id", "")
    updated_at = getattr(task, "updated_at", None) or getattr(task, "created_at", None)
    return DisableTaskSchema(
        id=str(task.id),
        incident_key=incident_key,
        fb_ad_id=task.fb_ad_id,
        ad_name=task.ad_name,
        status=task.status.value,
        attempt_count=task.attempt_count,
        last_error=task.last_error,
        next_retry_at=_serialize_optional_datetime(task.next_retry_at),
        requested_by_username=task.requested_by_username,
        created_at=task.created_at.isoformat(),
        updated_at=updated_at.isoformat() if updated_at else task.created_at.isoformat(),
        completed_at=_serialize_optional_datetime(task.completed_at),
    )


def _build_active_incident_schema(
    snapshot: AdSnapshot,
    *,
    alert_events: list[AlertEvent],
    disable_tasks: list[DisableTask],
) -> ActiveIncidentSchema:
    """Собирает API-представление текущего инцидента по snapshot и связанным данным."""
    incident_key = _incident_key_for_snapshot(snapshot)
    current_events = [event for event in alert_events if event.telegram_group_key == incident_key]
    current_tasks = [task for task in disable_tasks if task.open_state_token == incident_key]

    latest_event = max(current_events, key=lambda event: event.created_at, default=None)
    latest_task = max(current_tasks, key=_disable_task_activity_at, default=None)
    has_active_disable_task = any(
        task.status
        in (
            DisableTaskStatus.PENDING,
            DisableTaskStatus.RUNNING,
            DisableTaskStatus.RETRYING,
        )
        for task in current_tasks
    )
    auto_attempts = sum(
        1 for task in current_tasks if (task.requested_by_username or "") == "bot_auto_stop"
    )
    incident_retry_count = max(auto_attempts - 1, 0)
    needs_manual_attention = (
        snapshot.alert_state == AlertState.CLAIMED
        and snapshot.current_stage == AlertStage.STOP
        and not is_delivery_disabled(snapshot.delivery_status)
        and not has_active_disable_task
        and incident_retry_count >= SILENT_DISABLE_INCIDENT_RETRY_LIMIT
    )
    latest_activity_at = (
        _max_datetime(
            snapshot.updated_at,
            snapshot.last_observed_at,
            latest_event.created_at if latest_event else None,
            _disable_task_activity_at(latest_task) if latest_task else None,
        )
        or snapshot.updated_at
    )
    started_at = _min_datetime(
        min((event.created_at for event in current_events), default=None),
        min((task.created_at for task in current_tasks), default=None),
        snapshot.created_at,
    )

    return ActiveIncidentSchema(
        incident_key=incident_key,
        fb_ad_id=snapshot.fb_ad_id,
        ad_name=snapshot.ad_name,
        campaign_name=snapshot.campaign_name,
        adset_name=snapshot.adset_name,
        current_state=snapshot.alert_state.value,
        current_stage=snapshot.current_stage.value if snapshot.current_stage else None,
        delivery_status=snapshot.delivery_status,
        matched_rule_codes=_matched_rule_codes_for_snapshot(snapshot),
        reason_title=latest_event.reason_title if latest_event else None,
        reason_text=latest_event.reason_text if latest_event else None,
        metrics_json=latest_event.metrics_json
        if latest_event and latest_event.metrics_json
        else {},
        started_at=_serialize_optional_datetime(started_at),
        last_activity_at=latest_activity_at.isoformat(),
        last_observed_at=_serialize_optional_datetime(snapshot.last_observed_at),
        latest_alert_at=_serialize_optional_datetime(
            latest_event.created_at if latest_event else None
        ),
        latest_alert_stage=latest_event.stage.value
        if latest_event and latest_event.stage
        else None,
        latest_disable_task_status=(latest_task.status.value if latest_task else None),
        latest_disable_task_created_at=(
            _serialize_optional_datetime(latest_task.created_at) if latest_task else None
        ),
        latest_disable_task_updated_at=(
            _serialize_optional_datetime(latest_task.updated_at) if latest_task else None
        ),
        latest_disable_task_attempt=(latest_task.attempt_count if latest_task else None),
        latest_disable_task_id=(str(latest_task.id) if latest_task else None),
        latest_disable_task_last_error=(latest_task.last_error if latest_task else None),
        latest_disable_task_next_retry_at=(
            _serialize_optional_datetime(latest_task.next_retry_at) if latest_task else None
        ),
        latest_disable_task_completed_at=(
            _serialize_optional_datetime(latest_task.completed_at) if latest_task else None
        ),
        waiting_for_off=(
            snapshot.alert_state == AlertState.CLAIMED
            and not is_delivery_disabled(snapshot.delivery_status)
        ),
        has_active_disable_task=has_active_disable_task,
        incident_retry_count=incident_retry_count,
        needs_manual_attention=needs_manual_attention,
    )


def _serialize_enable_task(task: EnableTask) -> EnableTaskSchema:
    """Сериализует EnableTask для API-ответов."""
    updated_at = getattr(task, "updated_at", None) or task.created_at
    last_error = task.last_error
    next_retry_at = task.next_retry_at
    if task.status == EnableTaskStatus.SUCCEEDED:
        last_error = None
        next_retry_at = None
    return EnableTaskSchema(
        id=str(task.id),
        recommendation_event_id=(
            str(task.recommendation_event_id) if task.recommendation_event_id else None
        ),
        fb_ad_id=task.fb_ad_id,
        ad_name=task.ad_name,
        status=task.status.value,
        attempt_count=task.attempt_count,
        last_error=last_error,
        next_retry_at=next_retry_at.isoformat() if next_retry_at else None,
        requested_by_username=task.requested_by_username,
        created_at=task.created_at.isoformat(),
        updated_at=updated_at.isoformat(),
        completed_at=task.completed_at.isoformat() if task.completed_at else None,
    )


def _build_current_enable_tasks_query(
    *,
    created_since: datetime | None = None,
):
    """Строит запрос только по актуальной задаче на каждое объявление."""
    ranked_tasks = select(
        EnableTask.id.label("task_id"),
        func.row_number()
        .over(
            partition_by=EnableTask.fb_ad_id,
            order_by=[
                EnableTask.updated_at.desc(),
                EnableTask.created_at.desc(),
                EnableTask.id.desc(),
            ],
        )
        .label("row_num"),
    ).subquery()

    query = (
        select(EnableTask)
        .join(ranked_tasks, ranked_tasks.c.task_id == EnableTask.id)
        .join(
            EnableRecommendationEvent,
            EnableRecommendationEvent.id == EnableTask.recommendation_event_id,
            isouter=True,
        )
        .where(
            ranked_tasks.c.row_num == 1,
            EnableTask.status.in_(
                [
                    EnableTaskStatus.PENDING,
                    EnableTaskStatus.RUNNING,
                    EnableTaskStatus.RETRYING,
                    EnableTaskStatus.FAILED,
                    EnableTaskStatus.SUCCEEDED,
                ]
            ),
        )
    )
    if created_since is not None:
        query = query.where(
            or_(
                EnableRecommendationEvent.live_batch_started_at >= created_since,
                and_(
                    EnableRecommendationEvent.id.is_(None),
                    EnableTask.created_at >= created_since,
                ),
            )
        )
    return query.order_by(EnableTask.updated_at.desc(), EnableTask.created_at.desc())


def _serialize_enable_recommendation_event(
    event: EnableRecommendationEvent,
    *,
    current_batch_marker: datetime | None,
    related_task: EnableTask | None = None,
    current_snapshot: AdSnapshot | None = None,
    live_candidate: EnableRecommendationCandidate | None = None,
) -> EnableRecommendationEventSchema:
    """Сериализует recommendation event для dashboard."""
    state = "OPEN"
    if related_task is not None:
        state = "TASK_CREATED"
    elif current_batch_marker is None or event.live_batch_started_at != current_batch_marker:
        state = "STALE"

    recommendation_level = (
        live_candidate.recommendation_level
        if live_candidate is not None
        else event.recommendation_level
    )
    reason_title = live_candidate.reason_title if live_candidate is not None else event.reason_title
    reason_text = live_candidate.reason_text if live_candidate is not None else event.reason_text
    reason_title, reason_text = _normalize_enable_recommendation_reason(
        recommendation_level=recommendation_level,
        reason_title=reason_title,
        reason_text=reason_text,
    )
    metrics_json = (
        dict(live_candidate.metrics_json)
        if live_candidate is not None
        else _build_snapshot_metrics_json(current_snapshot)
        if current_snapshot
        else dict(event.metrics_json or {})
    )
    rule_summaries_source = (
        live_candidate.metrics_json if live_candidate is not None else (event.metrics_json or {})
    )
    rule_summaries = rule_summaries_source.get("rule_summaries")
    if isinstance(rule_summaries, list) and rule_summaries:
        metrics_json["rule_summaries"] = rule_summaries
    updated_at = getattr(event, "updated_at", None)
    if current_snapshot is not None and (
        updated_at is None or current_snapshot.last_observed_at > updated_at
    ):
        updated_at = current_snapshot.last_observed_at

    return EnableRecommendationEventSchema(
        id=str(event.id),
        fb_ad_id=event.fb_ad_id,
        ad_name=current_snapshot.ad_name if current_snapshot else event.ad_name,
        campaign_name=current_snapshot.campaign_name if current_snapshot else None,
        adset_name=current_snapshot.adset_name if current_snapshot else None,
        delivery_status=current_snapshot.delivery_status
        if current_snapshot
        else event.delivery_status,
        recommendation_level=recommendation_level.value,
        matched_rule_codes=(
            list(live_candidate.matched_rule_codes)
            if live_candidate is not None
            else event.matched_rule_codes or []
        ),
        reason_title=reason_title,
        reason_text=reason_text,
        metrics_json=metrics_json,
        live_batch_started_at=event.live_batch_started_at.isoformat(),
        created_at=event.created_at.isoformat(),
        updated_at=_serialize_optional_datetime(updated_at),
        state=state,
        related_enable_task_id=str(related_task.id) if related_task else None,
        related_enable_task_status=related_task.status.value if related_task else None,
    )


async def _load_ad_snapshots_by_fb_ad_id(
    db: AsyncSession,
    *,
    fb_ad_ids: list[str],
) -> dict[str, AdSnapshot]:
    """Загружает текущие snapshot по fb_ad_id."""
    if not fb_ad_ids:
        return {}

    result = await db.execute(select(AdSnapshot).where(AdSnapshot.fb_ad_id.in_(fb_ad_ids)))
    return {snapshot.fb_ad_id: snapshot for snapshot in result.scalars().all()}


async def _load_current_enable_recommendations(
    db: AsyncSession,
    *,
    limit: int | None = None,
) -> tuple[datetime | None, list[CurrentEnableRecommendationRow]]:
    """Загружает текущие рекомендации, подтверждённые live-переоценкой snapshot."""
    last_scan, batch_start = await load_live_batch_bounds(db)
    if last_scan is None or batch_start is None:
        return None, []

    current_batch_marker = compute_live_batch_marker(last_scan)
    result = await db.execute(
        select(EnableRecommendationEvent)
        .where(EnableRecommendationEvent.live_batch_started_at == current_batch_marker)
        .order_by(
            func.coalesce(
                EnableRecommendationEvent.updated_at, EnableRecommendationEvent.created_at
            ).desc(),
            EnableRecommendationEvent.created_at.desc(),
        )
    )
    events = result.scalars().all()
    snapshot_by_ad = await _load_ad_snapshots_by_fb_ad_id(
        db,
        fb_ad_ids=[event.fb_ad_id for event in events],
    )
    live_snapshots = [
        snapshot
        for snapshot in snapshot_by_ad.values()
        if is_within_live_batch(snapshot.last_observed_at, batch_start)
        and snapshot.delivery_status in RECOMMENDATION_DELIVERY_STATUSES
    ]
    live_candidates = await collect_enable_recommendation_candidates_for_snapshots(
        db,
        snapshots=live_snapshots,
        live_batch_started_at=current_batch_marker,
    )
    candidate_by_ad = {candidate.fb_ad_id: candidate for candidate in live_candidates}
    latest_by_ad: dict[str, CurrentEnableRecommendationRow] = {}
    for event in events:
        snapshot = snapshot_by_ad.get(event.fb_ad_id)
        if snapshot is None:
            continue
        if not is_within_live_batch(snapshot.last_observed_at, batch_start):
            continue
        if snapshot.delivery_status not in RECOMMENDATION_DELIVERY_STATUSES:
            continue
        candidate = candidate_by_ad.get(event.fb_ad_id)
        if candidate is None:
            continue
        if event.fb_ad_id not in latest_by_ad:
            latest_by_ad[event.fb_ad_id] = CurrentEnableRecommendationRow(
                event=event,
                snapshot=snapshot,
                candidate=candidate,
            )

    rows = list(latest_by_ad.values())
    if limit is not None:
        rows = rows[:limit]
    return current_batch_marker, rows


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


def _safe_decimal_percent_over(value: Decimal, baseline: Decimal) -> Decimal | None:
    """Возвращает процент превышения над базовым порогом или None."""
    baseline_decimal = Decimal(baseline)
    if baseline_decimal <= 0:
        return None
    ratio = Decimal(value) / baseline_decimal
    if ratio <= 1:
        return None
    return (ratio - Decimal("1")) * Decimal("100")


def _safe_decimal_percent_delta(value: Decimal, baseline: Decimal) -> Decimal | None:
    """Возвращает отклонение от базового порога со знаком."""
    baseline_decimal = Decimal(baseline)
    if baseline_decimal <= 0:
        return None
    ratio = Decimal(value) / baseline_decimal
    return (ratio - Decimal("1")) * Decimal("100")


def _percent_of_cpa(cpa_amount: Decimal, percent: Decimal) -> Decimal:
    """Возвращает абсолютный порог как процент от CPA."""
    return (Decimal(cpa_amount) * Decimal(percent)) / Decimal("100")


def _build_snapshot_base_budget_reference(
    snapshot: AdSnapshot,
    *,
    cpa_amount: Decimal,
    rule_config: OfferRuleConfig,
) -> dict[str, object] | None:
    """Возвращает базовый бюджет объявления по самой глубокой доступной стадии."""
    clicks = int(snapshot.clicks or 0)
    leads = int(snapshot.leads or 0)
    registrations = int(snapshot.registrations or 0)
    deposits = int(snapshot.deposits or 0)
    spend = Decimal(snapshot.spend or 0)
    cpa_decimal = Decimal(cpa_amount)

    cpc_budget = (
        _percent_of_cpa(cpa_decimal, Decimal(rule_config.cpc_percent_stop))
        if rule_config.cpc_percent_enabled
        else None
    )
    cpl_budget = (
        _percent_of_cpa(cpa_decimal, Decimal(rule_config.cpl_percent_stop))
        if rule_config.cpl_percent_enabled
        else None
    )
    cpr_budget = (
        _percent_of_cpa(cpa_decimal, Decimal(rule_config.cpr_percent_stop))
        if rule_config.cpr_percent_enabled
        else None
    )

    label: str | None = None
    ideal_spend: Decimal | None = None

    if deposits >= 1 and registrations >= 1 and cpa_decimal > 0:
        label = "CPA"
        ideal_spend = cpa_decimal * Decimal(deposits)
    elif registrations >= 1:
        if cpr_budget is not None and cpr_budget > 0:
            label = "CPR"
            ideal_spend = cpr_budget * Decimal(registrations)
        elif rule_config.spend_no_dep_enabled:
            label = "Расход без депозита"
            ideal_spend = _percent_of_cpa(
                cpa_decimal, Decimal(rule_config.spend_no_dep_from_percent)
            )
    elif leads >= 1:
        if cpl_budget is not None and cpl_budget > 0:
            label = "CPL"
            ideal_spend = cpl_budget * Decimal(leads)
        elif cpr_budget is not None and cpr_budget > 0:
            label = "Расход до регистрации"
            ideal_spend = cpr_budget
    elif clicks >= 1:
        if cpc_budget is not None and cpc_budget > 0:
            label = "CPC"
            ideal_spend = cpc_budget * Decimal(clicks)
        elif cpl_budget is not None and cpl_budget > 0:
            label = "Расход до лида"
            ideal_spend = cpl_budget
    elif cpc_budget is not None and cpc_budget > 0:
        label = "Расход до клика"
        ideal_spend = cpc_budget

    if label is None or ideal_spend is None or ideal_spend <= 0:
        return None

    overrun_amount = spend - ideal_spend
    overrun_percent = _safe_decimal_percent_over(spend, ideal_spend)
    return {
        "label": label,
        "actual_spend": spend,
        "ideal_spend": ideal_spend,
        "overrun_amount": overrun_amount,
        "overrun_percent": overrun_percent,
    }


async def _load_offer_rules_for_snapshots(
    db: AsyncSession,
    snapshots: list[AdSnapshot],
) -> dict[_uuid.UUID, tuple[Offer, OfferRuleConfig]]:
    """Загружает offer + rule config для списка snapshot с offer_id."""
    offer_ids = {snapshot.offer_id for snapshot in snapshots if snapshot.offer_id is not None}
    if not offer_ids:
        return {}

    result = await db.execute(
        select(Offer, OfferRuleConfig)
        .join(OfferRuleConfig, OfferRuleConfig.offer_id == Offer.id)
        .where(Offer.id.in_(offer_ids))
    )
    return {offer.id: (offer, rule_config) for offer, rule_config in result.all()}


def _build_campaign_stop_overrun_rows(
    snapshots: list[AdSnapshot],
    offer_rule_map: dict[_uuid.UUID, tuple[Offer, OfferRuleConfig]],
) -> list[dict]:
    """Возвращает отклонение от базовой экономики в разрезе кампаний."""
    grouped: dict[str, dict[str, object]] = {}

    for snapshot in snapshots:
        if not snapshot.campaign_name or snapshot.offer_id is None:
            continue
        offer_bundle = offer_rule_map.get(snapshot.offer_id)
        if offer_bundle is None:
            continue

        offer, rule_config = offer_bundle
        budget_reference = _build_snapshot_base_budget_reference(
            snapshot,
            cpa_amount=Decimal(offer.cpa_amount),
            rule_config=rule_config,
        )
        if budget_reference is None:
            continue

        campaign_name = snapshot.campaign_name
        actual_spend = Decimal(budget_reference["actual_spend"])
        ideal_spend = Decimal(budget_reference["ideal_spend"])
        overrun_amount = Decimal(budget_reference["overrun_amount"])
        overrun_percent = budget_reference["overrun_percent"]
        bucket = grouped.setdefault(
            campaign_name,
            {
                "campaign": campaign_name[:30] + "…" if len(campaign_name) > 30 else campaign_name,
                "campaign_full": campaign_name,
                "actual_spend_sum": Decimal("0"),
                "ideal_spend_sum": Decimal("0"),
                "total_ads": 0,
                "affected_ads": 0,
                "over_budget_ads": 0,
                "under_budget_ads": 0,
                "on_target_ads": 0,
                "dominant_metric": None,
                "top_ad_name": None,
                "max_ad_overrun_amount": Decimal("0"),
                "max_ad_overrun_percent": Decimal("0"),
            },
        )
        bucket["total_ads"] = int(bucket["total_ads"]) + 1
        bucket["actual_spend_sum"] = Decimal(bucket["actual_spend_sum"]) + actual_spend
        bucket["ideal_spend_sum"] = Decimal(bucket["ideal_spend_sum"]) + ideal_spend

        if overrun_amount > 0:
            bucket["affected_ads"] = int(bucket["affected_ads"]) + 1
            bucket["over_budget_ads"] = int(bucket["over_budget_ads"]) + 1
        elif overrun_amount < 0:
            bucket["under_budget_ads"] = int(bucket["under_budget_ads"]) + 1
        else:
            bucket["on_target_ads"] = int(bucket["on_target_ads"]) + 1

        if overrun_amount > Decimal(bucket["max_ad_overrun_amount"]):
            bucket["max_ad_overrun_amount"] = overrun_amount
            bucket["max_ad_overrun_percent"] = (
                Decimal(overrun_percent) if overrun_percent is not None else Decimal("0")
            )
            bucket["dominant_metric"] = budget_reference["label"]
            bucket["top_ad_name"] = snapshot.ad_name

    rows: list[dict[str, object]] = []
    for item in grouped.values():
        ideal_spend = Decimal(item["ideal_spend_sum"])
        actual_spend = Decimal(item["actual_spend_sum"])
        budget_delta_amount = actual_spend - ideal_spend
        budget_delta_percent = _safe_decimal_percent_delta(actual_spend, ideal_spend)
        if budget_delta_percent is None:
            continue
        if budget_delta_amount > 0:
            budget_status = "OVER"
        elif budget_delta_amount < 0:
            budget_status = "UNDER"
        else:
            budget_status = "ON_TARGET"
        rows.append(
            {
                **item,
                "actual_spend": actual_spend,
                "ideal_spend": ideal_spend,
                "budget_delta_amount": budget_delta_amount,
                "budget_delta_percent": budget_delta_percent,
                "budget_status": budget_status,
                "overrun_amount": budget_delta_amount,
                "overrun_percent": budget_delta_percent,
            }
        )

    rows = sorted(
        rows,
        key=lambda item: (
            0
            if str(item["budget_status"]) == "OVER"
            else 1
            if str(item["budget_status"]) == "UNDER"
            else 2,
            -abs(Decimal(item["budget_delta_percent"])),
            -abs(Decimal(item["budget_delta_amount"])),
            -int(item["over_budget_ads"]),
            str(item["campaign_full"]),
        ),
    )
    return [
        {
            "campaign": row["campaign"],
            "campaign_full": row["campaign_full"],
            "budget_delta_percent": round(float(Decimal(row["budget_delta_percent"])), 1),
            "budget_delta_amount": round(float(Decimal(row["budget_delta_amount"])), 2),
            "budget_status": row["budget_status"],
            "overrun_percent": round(float(Decimal(row["overrun_percent"])), 1),
            "actual_spend": round(float(Decimal(row["actual_spend"])), 2),
            "ideal_spend": round(float(Decimal(row["ideal_spend"])), 2),
            "overrun_amount": round(float(Decimal(row["overrun_amount"])), 2),
            "total_ads": int(row["total_ads"]),
            "affected_ads": int(row["affected_ads"]),
            "over_budget_ads": int(row["over_budget_ads"]),
            "under_budget_ads": int(row["under_budget_ads"]),
            "on_target_ads": int(row["on_target_ads"]),
            "dominant_metric": row["dominant_metric"],
            "top_ad_name": row["top_ad_name"],
            "max_ad_overrun_amount": round(float(Decimal(row["max_ad_overrun_amount"])), 2),
            "max_ad_overrun_percent": round(float(Decimal(row["max_ad_overrun_percent"])), 1),
        }
        for row in rows
    ]


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


def _serialize_observer_runtime_fields(
    row: ObserverSettings | None,
) -> dict[str, str | None]:
    """Сериализует runtime-статус observer для dashboard."""
    if row is None:
        return {
            "observer_status": None,
            "observer_status_message": None,
            "observer_heartbeat_at": None,
            "observer_last_error": None,
            "observer_last_error_at": None,
        }

    return {
        "observer_status": row.worker_status,
        "observer_status_message": row.worker_message,
        "observer_heartbeat_at": (
            row.worker_heartbeat_at.isoformat() if row.worker_heartbeat_at else None
        ),
        "observer_last_error": row.worker_last_error,
        "observer_last_error_at": (
            row.worker_last_error_at.isoformat() if row.worker_last_error_at else None
        ),
    }


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
    normalized_codes = {
        _offer_code_lookup_key(code) for code in offer_codes if _offer_code_lookup_key(code)
    }
    if not normalized_codes:
        return {}

    result = await db.execute(
        select(
            Offer.code,
            OfferRuleConfig.frequency_elevated_threshold,
            OfferRuleConfig.frequency_critical_threshold,
        )
        .join(OfferRuleConfig, OfferRuleConfig.offer_id == Offer.id)
        .where(func.lower(Offer.code).in_(normalized_codes))
    )
    return {
        code.casefold(): (Decimal(elevated), Decimal(critical))
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
        offer_code_getter=lambda snapshot: _offer_code_lookup_key(snapshot.resolved_offer_code),
        cpm_getter=lambda snapshot: snapshot.cpm,
    )
    frequency_thresholds = await _load_frequency_thresholds_by_offer(
        db,
        offer_codes={
            snapshot.resolved_offer_code for snapshot in snapshots if snapshot.resolved_offer_code
        },
    )

    diagnostics_map: dict[str, AdDiagnosticsSchema] = {}
    for snapshot in snapshots:
        offer_code_key = _offer_code_lookup_key(snapshot.resolved_offer_code)
        elevated_threshold, critical_threshold = frequency_thresholds.get(
            offer_code_key,
            (Decimal("2"), Decimal("3")),
        )
        diagnostics = build_ad_quality_diagnostics(
            cpm_value=snapshot.cpm,
            cpm_baseline=cpm_baselines.get(offer_code_key),
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
    observer_row = await db.scalar(
        select(ObserverSettings).where(ObserverSettings.singleton_key == "default")
    )
    cabinet_day_start = observer_row.cabinet_day_started_at if observer_row else None
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
            .where(
                DisableTask.status.in_(
                    [
                        DisableTaskStatus.PENDING,
                        DisableTaskStatus.RETRYING,
                        DisableTaskStatus.RUNNING,
                    ]
                )
            )
        )
        or 0
    )
    pending_enable_tasks = (
        await db.scalar(
            (
                select(func.count())
                .select_from(EnableTask)
                .join(
                    EnableRecommendationEvent,
                    EnableRecommendationEvent.id == EnableTask.recommendation_event_id,
                    isouter=True,
                )
                .where(
                    EnableTask.status.in_(
                        [
                            EnableTaskStatus.PENDING,
                            EnableTaskStatus.RETRYING,
                            EnableTaskStatus.RUNNING,
                        ]
                    )
                )
                .where(
                    or_(
                        EnableRecommendationEvent.live_batch_started_at >= cabinet_day_start,
                        and_(
                            EnableRecommendationEvent.id.is_(None),
                            EnableTask.created_at >= cabinet_day_start,
                        ),
                    )
                )
            )
            if cabinet_day_start is not None
            else select(func.count())
            .select_from(EnableTask)
            .where(
                EnableTask.status.in_(
                    [
                        EnableTaskStatus.PENDING,
                        EnableTaskStatus.RETRYING,
                        EnableTaskStatus.RUNNING,
                    ]
                )
            )
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
    _, current_enable_recommendations = await _load_current_enable_recommendations(db)
    enable_recommendations_ok = sum(
        1
        for row in current_enable_recommendations
        if row.candidate.recommendation_level == EnableRecommendationLevel.OK
    )
    enable_recommendations_early_signal = sum(
        1
        for row in current_enable_recommendations
        if row.candidate.recommendation_level == EnableRecommendationLevel.EARLY_SIGNAL
    )
    enable_recommendations_warning = sum(
        1
        for row in current_enable_recommendations
        if row.candidate.recommendation_level == EnableRecommendationLevel.WARNING
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
        pending_enable_tasks=pending_enable_tasks,
        enable_recommendations_ok=enable_recommendations_ok,
        enable_recommendations_early_signal=enable_recommendations_early_signal,
        enable_recommendations_warning=enable_recommendations_warning,
        last_scan_at=last_scan_str,
        **_serialize_observer_runtime_fields(observer_row),
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


@app.get("/api/dashboard/batch", response_model=DashboardBatchSchema)
async def get_dashboard_batch(
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    """Один запрос вместо 4 для AdsPage.

    Возвращает ads, stats, incidents и disable-tasks одновременно.
    """
    # 1. Получить stats (полная логика из get_dashboard_stats)
    last_scan = await db.scalar(select(func.max(AdSnapshot.last_observed_at)))
    scan_cutoff = _current_scan_cutoff(last_scan)

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

    observer_row = await db.scalar(
        select(ObserverSettings).where(ObserverSettings.singleton_key == "default")
    )
    cabinet_day_start = observer_row.cabinet_day_started_at if observer_row else None
    disabled_since = cabinet_day_start or scan_cutoff

    active_offers = (
        await db.scalar(select(func.count()).select_from(Offer).where(Offer.is_active.is_(True)))
        or 0
    )
    pending_tasks = (
        await db.scalar(
            select(func.count())
            .select_from(DisableTask)
            .where(
                DisableTask.status.in_(
                    [
                        DisableTaskStatus.PENDING,
                        DisableTaskStatus.RETRYING,
                        DisableTaskStatus.RUNNING,
                    ]
                )
            )
        )
        or 0
    )
    pending_enable_tasks = (
        await db.scalar(
            (
                select(func.count())
                .select_from(EnableTask)
                .join(
                    EnableRecommendationEvent,
                    EnableRecommendationEvent.id == EnableTask.recommendation_event_id,
                    isouter=True,
                )
                .where(
                    EnableTask.status.in_(
                        [
                            EnableTaskStatus.PENDING,
                            EnableTaskStatus.RETRYING,
                            EnableTaskStatus.RUNNING,
                        ]
                    )
                )
                .where(
                    or_(
                        EnableRecommendationEvent.live_batch_started_at >= cabinet_day_start,
                        and_(
                            EnableRecommendationEvent.id.is_(None),
                            EnableTask.created_at >= cabinet_day_start,
                        ),
                    )
                )
            )
            if cabinet_day_start is not None
            else select(func.count())
            .select_from(EnableTask)
            .where(
                EnableTask.status.in_(
                    [
                        EnableTaskStatus.PENDING,
                        EnableTaskStatus.RETRYING,
                        EnableTaskStatus.RUNNING,
                    ]
                )
            )
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
    _, current_enable_recommendations = await _load_current_enable_recommendations(db)
    enable_recommendations_ok = sum(
        1
        for row in current_enable_recommendations
        if row.candidate.recommendation_level == EnableRecommendationLevel.OK
    )
    enable_recommendations_early_signal = sum(
        1
        for row in current_enable_recommendations
        if row.candidate.recommendation_level == EnableRecommendationLevel.EARLY_SIGNAL
    )
    enable_recommendations_warning = sum(
        1
        for row in current_enable_recommendations
        if row.candidate.recommendation_level == EnableRecommendationLevel.WARNING
    )

    stats = DashboardStatsSchema(
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
        pending_enable_tasks=pending_enable_tasks,
        enable_recommendations_ok=enable_recommendations_ok,
        enable_recommendations_early_signal=enable_recommendations_early_signal,
        enable_recommendations_warning=enable_recommendations_warning,
        last_scan_at=last_scan.isoformat() if last_scan else None,
        **_serialize_observer_runtime_fields(observer_row),
    )

    # 2. Получить ads (переиспользуем логику из list_ad_snapshots)
    q = select(AdSnapshot).order_by(AdSnapshot.last_observed_at.desc()).limit(limit)
    result = await db.execute(q)
    snapshots = result.scalars().all()
    diagnostics_map = await _build_snapshot_diagnostics_map(db, snapshots)

    ads = [
        AdSnapshotSchema(
            id=str(s.id),
            fb_ad_id=s.fb_ad_id,
            campaign_name=s.campaign_name,
            adset_name=s.adset_name,
            ad_name=s.ad_name,
            delivery_status=s.delivery_status,
            offer_code=s.resolved_offer_code,
            spend=s.spend,
            budget=getattr(s, "budget", "") or "",
            reach=int(getattr(s, "reach", 0) or 0),
            impressions=int(getattr(s, "impressions", 0) or 0),
            clicks=s.clicks,
            cpc=s.cpc,
            ctr=getattr(s, "ctr", None),
            outbound_clicks=s.outbound_clicks,
            outbound_ctr=s.outbound_ctr,
            landing_page_views=s.landing_page_views,
            cost_per_result=getattr(s, "cost_per_result", None),
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
            cpm_diagnostic_status=diagnostics_map[s.fb_ad_id].cpm.status
            if s.fb_ad_id in diagnostics_map
            else None,
            frequency_diagnostic_status=(
                diagnostics_map[s.fb_ad_id].frequency.status
                if s.fb_ad_id in diagnostics_map
                else None
            ),
            diagnostic_short_text=(
                diagnostics_map[s.fb_ad_id].summary_text if s.fb_ad_id in diagnostics_map else None
            ),
            last_observed_at=(s.last_observed_at.isoformat() if s.last_observed_at else None),
        )
        for s in snapshots
    ]

    # 3. Получить incidents (переиспользуем логику из list_active_incidents)
    snapshot_query = (
        select(AdSnapshot)
        .where(
            AdSnapshot.last_observed_at >= scan_cutoff,
            AdSnapshot.alert_state.in_(
                [
                    AlertState.EARLY_SIGNAL_SENT,
                    AlertState.WARNING_SENT,
                    AlertState.STOP_SENT,
                    AlertState.CLAIMED,
                ]
            ),
        )
        .order_by(AdSnapshot.last_observed_at.desc())
    )

    incident_snapshots = (await db.execute(snapshot_query)).scalars().all()
    incidents: list[ActiveIncidentSchema] = []

    if incident_snapshots:
        incident_fb_ad_ids = [snapshot.fb_ad_id for snapshot in incident_snapshots]
        alert_events = (
            (
                await db.execute(
                    select(AlertEvent)
                    .where(AlertEvent.fb_ad_id.in_(incident_fb_ad_ids))
                    .order_by(AlertEvent.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        disable_tasks_for_incidents = (
            (
                await db.execute(
                    select(DisableTask)
                    .where(DisableTask.fb_ad_id.in_(incident_fb_ad_ids))
                    .order_by(DisableTask.updated_at.desc(), DisableTask.created_at.desc())
                )
            )
            .scalars()
            .all()
        )

        events_by_ad: dict[str, list[AlertEvent]] = {}
        for event in alert_events:
            events_by_ad.setdefault(event.fb_ad_id, []).append(event)

        tasks_by_ad: dict[str, list[DisableTask]] = {}
        for task in disable_tasks_for_incidents:
            tasks_by_ad.setdefault(task.fb_ad_id, []).append(task)

        incidents = [
            _build_active_incident_schema(
                snapshot,
                alert_events=events_by_ad.get(snapshot.fb_ad_id, []),
                disable_tasks=tasks_by_ad.get(snapshot.fb_ad_id, []),
            )
            for snapshot in incident_snapshots
        ]
        incidents.sort(key=lambda incident: incident.last_activity_at, reverse=True)

    # 4. Получить disable-tasks (переиспользуем логику из list_disable_tasks)
    q_tasks = select(DisableTask).order_by(
        DisableTask.updated_at.desc(), DisableTask.created_at.desc()
    )
    q_tasks = q_tasks.where(
        DisableTask.status.in_(
            [
                DisableTaskStatus.PENDING,
                DisableTaskStatus.RUNNING,
                DisableTaskStatus.RETRYING,
                DisableTaskStatus.FAILED,
            ]
        )
    )
    q_tasks = q_tasks.limit(50)

    result_tasks = await db.execute(q_tasks)
    tasks = result_tasks.scalars().all()
    disable_tasks = [_serialize_disable_task(t) for t in tasks]

    return DashboardBatchSchema(
        ads=ads,
        stats=stats,
        incidents=incidents[:50],
        disable_tasks=disable_tasks,
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
        q = q.where(
            func.lower(AdSnapshot.resolved_offer_code) == _offer_code_lookup_key(offer_code)
        )
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
            budget=getattr(s, "budget", "") or "",
            reach=int(getattr(s, "reach", 0) or 0),
            impressions=int(getattr(s, "impressions", 0) or 0),
            clicks=s.clicks,
            cpc=s.cpc,
            ctr=getattr(s, "ctr", None),
            outbound_clicks=s.outbound_clicks,
            outbound_ctr=s.outbound_ctr,
            landing_page_views=s.landing_page_views,
            cost_per_result=getattr(s, "cost_per_result", None),
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
            cpm_diagnostic_status=diagnostics_map[s.fb_ad_id].cpm.status
            if s.fb_ad_id in diagnostics_map
            else None,
            frequency_diagnostic_status=(
                diagnostics_map[s.fb_ad_id].frequency.status
                if s.fb_ad_id in diagnostics_map
                else None
            ),
            diagnostic_short_text=(
                diagnostics_map[s.fb_ad_id].summary_text if s.fb_ad_id in diagnostics_map else None
            ),
            last_observed_at=(s.last_observed_at.isoformat() if s.last_observed_at else None),
        )
        for s in snapshots
    ]


@app.get("/api/dashboard/incidents", response_model=list[ActiveIncidentSchema])
async def list_active_incidents(
    fb_ad_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Текущие открытые инциденты из актуальной скан-сессии."""
    last_scan = await db.scalar(select(func.max(AdSnapshot.last_observed_at)))
    scan_cutoff = _current_scan_cutoff(last_scan)

    snapshot_query = (
        select(AdSnapshot)
        .where(
            AdSnapshot.last_observed_at >= scan_cutoff,
            AdSnapshot.alert_state.in_(
                [
                    AlertState.EARLY_SIGNAL_SENT,
                    AlertState.WARNING_SENT,
                    AlertState.STOP_SENT,
                    AlertState.CLAIMED,
                ]
            ),
        )
        .order_by(AdSnapshot.last_observed_at.desc())
    )
    if fb_ad_id:
        snapshot_query = snapshot_query.where(AdSnapshot.fb_ad_id == fb_ad_id)

    snapshots = (await db.execute(snapshot_query)).scalars().all()
    if not snapshots:
        return []

    fb_ad_ids = [snapshot.fb_ad_id for snapshot in snapshots]
    alert_events = (
        (
            await db.execute(
                select(AlertEvent)
                .where(AlertEvent.fb_ad_id.in_(fb_ad_ids))
                .order_by(AlertEvent.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    disable_tasks = (
        (
            await db.execute(
                select(DisableTask)
                .where(DisableTask.fb_ad_id.in_(fb_ad_ids))
                .order_by(DisableTask.updated_at.desc(), DisableTask.created_at.desc())
            )
        )
        .scalars()
        .all()
    )

    events_by_ad: dict[str, list[AlertEvent]] = {}
    for event in alert_events:
        events_by_ad.setdefault(event.fb_ad_id, []).append(event)

    tasks_by_ad: dict[str, list[DisableTask]] = {}
    for task in disable_tasks:
        tasks_by_ad.setdefault(task.fb_ad_id, []).append(task)

    incidents = [
        _build_active_incident_schema(
            snapshot,
            alert_events=events_by_ad.get(snapshot.fb_ad_id, []),
            disable_tasks=tasks_by_ad.get(snapshot.fb_ad_id, []),
        )
        for snapshot in snapshots
    ]
    incidents.sort(key=lambda incident: incident.last_activity_at, reverse=True)
    return incidents[:limit]


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
            incident_key=e.telegram_group_key,
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


def _is_disable_task_stale_for_manual_restart(task: DisableTask, *, now: datetime) -> bool:
    """Проверяет, что RUNNING-задача действительно зависла."""
    last_activity_at = task.updated_at or task.created_at
    return last_activity_at <= now - DISABLE_TASK_STALE_TIMEOUT


@app.post("/api/dashboard/disable-tasks/{task_id}/retry")
async def retry_disable_task(task_id: str, db: AsyncSession = Depends(get_db)):
    """Принудительно возвращает задачу отключения в очередь."""
    result = await db.execute(select(DisableTask).where(DisableTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    now = datetime.now(UTC)
    if task.status == DisableTaskStatus.RUNNING:
        if not _is_disable_task_stale_for_manual_restart(task, now=now):
            raise HTTPException(
                status_code=400, detail="Задача ещё выполняется и не считается зависшей"
            )
    elif task.status not in (DisableTaskStatus.RETRYING, DisableTaskStatus.FAILED):
        raise HTTPException(
            status_code=400, detail="Задача не в состоянии retry/failed/stale-running"
        )

    task.status = DisableTaskStatus.PENDING
    task.next_retry_at = None
    task.last_error = None
    task.completed_at = None
    await db.commit()
    return {"ok": True}


@app.post("/api/dashboard/disable-tasks", response_model=DisableTaskSchema, status_code=201)
async def create_disable_task(
    body: CreateDisableTaskRequest,
    db: AsyncSession = Depends(get_db),
) -> DisableTaskSchema:
    """Создаёт задачу на отключение объявления с идемпотентностью.

    Ищет AdSnapshot по fb_ad_id, затем создаёт DisableTask с использованием
    open_state_token как incident_key. Если задача с тем же fb_ad_id,
    incident_key и статусом PENDING/RUNNING/RETRYING уже существует,
    возвращает существующую задачу со статусом 200.
    """
    snapshot = await db.scalar(select(AdSnapshot).where(AdSnapshot.fb_ad_id == body.fb_ad_id))
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Снэпшот объявления не найден")

    incident_key = snapshot.open_state_token
    if incident_key is None:
        incident_key = str(_uuid.uuid4())
        snapshot.open_state_token = incident_key
        await db.flush()

    existing_task = await db.scalar(
        select(DisableTask).where(
            and_(
                DisableTask.fb_ad_id == body.fb_ad_id,
                DisableTask.open_state_token == incident_key,
                DisableTask.status.in_(
                    [
                        DisableTaskStatus.PENDING,
                        DisableTaskStatus.RUNNING,
                        DisableTaskStatus.RETRYING,
                    ]
                ),
            )
        )
    )
    if existing_task is not None:
        await db.rollback()
        return _serialize_disable_task(existing_task)

    new_task = DisableTask(
        fb_ad_id=body.fb_ad_id,
        open_state_token=incident_key,
        status=DisableTaskStatus.PENDING,
        requested_by_username="dashboard",
    )
    db.add(new_task)
    await db.commit()
    await db.refresh(new_task)
    return _serialize_disable_task(new_task)


@app.get("/api/dashboard/disable-tasks", response_model=list[DisableTaskSchema])
async def list_disable_tasks(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Задачи на отключение (для мониторинга)."""
    q = select(DisableTask).order_by(DisableTask.updated_at.desc(), DisableTask.created_at.desc())
    if status:
        q = q.where(DisableTask.status == DisableTaskStatus(status))
    else:
        q = q.where(
            DisableTask.status.in_(
                [
                    DisableTaskStatus.PENDING,
                    DisableTaskStatus.RUNNING,
                    DisableTaskStatus.RETRYING,
                    DisableTaskStatus.FAILED,
                ]
            )
        )
    q = q.limit(limit).offset(offset)

    result = await db.execute(q)
    tasks = result.scalars().all()
    return [_serialize_disable_task(t) for t in tasks]


@app.get(
    "/api/dashboard/enable-recommendations",
    response_model=list[EnableRecommendationEventSchema],
)
async def list_enable_recommendations(
    limit: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Рекомендации на включение из текущего живого батча."""
    current_batch_marker, rows = await _load_current_enable_recommendations(db, limit=limit)
    if not rows:
        return []

    event_ids = [row.event.id for row in rows]
    tasks_result = await db.execute(
        select(EnableTask)
        .where(EnableTask.recommendation_event_id.in_(event_ids))
        .order_by(EnableTask.created_at.desc())
    )
    task_by_event_id: dict[_uuid.UUID, EnableTask] = {}
    for task in tasks_result.scalars().all():
        if task.recommendation_event_id and task.recommendation_event_id not in task_by_event_id:
            task_by_event_id[task.recommendation_event_id] = task

    return [
        _serialize_enable_recommendation_event(
            row.event,
            current_batch_marker=current_batch_marker,
            related_task=task_by_event_id.get(row.event.id),
            current_snapshot=row.snapshot,
            live_candidate=row.candidate,
        )
        for row in rows
    ]


@app.post("/api/dashboard/enable-recommendations/{event_id}/enable")
async def create_enable_task_from_recommendation(
    event_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Создаёт или переиспользует задачу на включение по recommendation event."""
    result = await promote_recommendation_to_enable_task(
        db,
        event_id=event_id,
        requested_by_username="dashboard",
    )
    if result.outcome in {"recommendation_not_found", "snapshot_not_found"}:
        raise HTTPException(status_code=404, detail=result.detail)
    if result.outcome not in {"created", "existing", "requeued"}:
        raise HTTPException(status_code=409, detail=result.detail)

    await db.commit()
    task = None
    if result.task_id:
        task = await db.scalar(
            select(EnableTask).where(EnableTask.id == _uuid.UUID(result.task_id))
        )

    return {
        "ok": True,
        "created_new": result.created_new,
        "detail": result.detail,
        "task": _serialize_enable_task(task).model_dump() if task else None,
    }


@app.get("/api/dashboard/enable-tasks", response_model=list[EnableTaskSchema])
async def list_enable_tasks(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Задачи на включение для мониторинга.

    По умолчанию показываем только актуальную последнюю задачу на каждое объявление,
    чтобы старые ошибки не маскировались под текущее состояние после успешного повтора.
    """
    if status:
        q = (
            select(EnableTask)
            .where(EnableTask.status == EnableTaskStatus(status))
            .order_by(EnableTask.updated_at.desc(), EnableTask.created_at.desc())
        )
    else:
        q = _build_current_enable_tasks_query(created_since=await _get_cabinet_day_start(db))
    q = q.limit(limit).offset(offset)

    result = await db.execute(q)
    tasks = result.scalars().all()
    return [_serialize_enable_task(task) for task in tasks]


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
        q = q.where(
            func.lower(AdSnapshot.resolved_offer_code) == _offer_code_lookup_key(offer_code)
        )

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
    alerts_by_hour = [row for _, row in sorted(alerts_timeline.items(), key=lambda item: item[0])]

    # 2. Кампании за период собираем тем же способом, что верхний performance-блок.
    snapshot_result = await db.execute(
        select(AdSnapshot)
        .where(AdSnapshot.last_observed_at >= snapshot_cutoff)
        .order_by(AdSnapshot.last_observed_at.asc())
    )
    snapshots = snapshot_result.scalars().all()
    rule_violations = _build_current_risk_reason_rows(snapshots)
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
    offer_rule_map = await _load_offer_rules_for_snapshots(db, snapshots)
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
    campaign_budget_deltas = _build_campaign_stop_overrun_rows(snapshots, offer_rule_map)

    # 3. Распределение статусов — только по актуальному живому срезу.
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

    # 4. Топ объявлений по расходу — текущий живой срез, без исторического режима.
    top_ads_result = await db.execute(
        select(
            AdSnapshot.ad_name,
            AdSnapshot.adset_name,
            AdSnapshot.fb_ad_id,
            AdSnapshot.spend,
            AdSnapshot.clicks,
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
            "adset_short": row.adset_name[:18] + "…"
            if len(row.adset_name) > 18
            else row.adset_name,
            "label": (
                f"{row.ad_name[:16] + '…' if len(row.ad_name) > 16 else row.ad_name} · "
                f"{row.adset_name[:10] + '…' if len(row.adset_name) > 10 else row.adset_name}"
            ),
            "fb_ad_id": row.fb_ad_id,
            "spend": float(row.spend or 0),
            "clicks": int(row.clicks or 0),
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
        campaign_budget_deltas=campaign_budget_deltas,
        campaign_stop_overruns=campaign_budget_deltas,
    )


# ==========================================
# Эндпоинт — Таймлайн объявления
# ==========================================


@app.get("/api/ads/{fb_ad_id}/timeline")
async def get_ad_timeline(fb_ad_id: str, db: AsyncSession = Depends(get_db)):
    """Таймлайн событий по одному объявлению: алерты, метрики на каждый момент, динамика расхода."""
    # Текущий снэпшот
    snapshot_result = await db.execute(select(AdSnapshot).where(AdSnapshot.fb_ad_id == fb_ad_id))
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

    recommendation_events_result = await db.execute(
        select(EnableRecommendationEvent)
        .where(EnableRecommendationEvent.fb_ad_id == fb_ad_id)
        .order_by(EnableRecommendationEvent.created_at.desc())
    )
    recommendation_events = recommendation_events_result.scalars().all()

    enable_tasks_result = await db.execute(
        select(EnableTask)
        .where(EnableTask.fb_ad_id == fb_ad_id)
        .order_by(EnableTask.created_at.desc())
    )
    enable_tasks = enable_tasks_result.scalars().all()

    diagnostics = None
    if snapshot is not None:
        diagnostics_map = await _build_snapshot_diagnostics_map(db, [snapshot])
        diagnostics = diagnostics_map.get(snapshot.fb_ad_id)
    current_incident_key = _incident_key_for_snapshot(snapshot) if snapshot is not None else None
    current_incident = (
        _build_active_incident_schema(
            snapshot,
            alert_events=events,
            disable_tasks=tasks,
        )
        if snapshot is not None
        and snapshot.alert_state
        in (
            AlertState.EARLY_SIGNAL_SENT,
            AlertState.WARNING_SENT,
            AlertState.STOP_SENT,
            AlertState.CLAIMED,
        )
        else None
    )

    # Формируем таймлайн: объединяем алерты и задачи по времени
    timeline = []
    for e in events:
        m = e.metrics_json or {}
        timeline.append(
            {
                "type": "alert",
                "time": e.created_at.isoformat(),
                "stage": e.stage.value if e.stage else None,
                "state": e.state.value if e.state else None,
                "incident_key": e.telegram_group_key,
                "current_incident": bool(
                    current_incident_key and e.telegram_group_key == current_incident_key
                ),
                "matched_rules": e.matched_rule_codes or [],
                "reason_title": e.reason_title,
                "reason_text": e.reason_text,
                "spend": m.get("spend"),
                "budget": m.get("budget"),
                "reach": m.get("reach"),
                "impressions": m.get("impressions"),
                "clicks": m.get("clicks"),
                "cpc": m.get("cpc"),
                "ctr": m.get("ctr"),
                "outbound_clicks": m.get("outbound_clicks"),
                "outbound_ctr": m.get("outbound_ctr"),
                "landing_page_views": m.get("landing_page_views"),
                "cost_per_result": m.get("cost_per_result"),
                "cost_per_landing_page_view": m.get("cost_per_landing_page_view"),
                "cpm": m.get("cpm"),
                "frequency": m.get("frequency"),
                "leads": m.get("leads"),
                "registrations": m.get("registrations"),
                "cost_per_registration": m.get("cost_per_registration"),
                "deposits": m.get("deposits"),
            }
        )
    for t in tasks:
        timeline.append(
            {
                "type": "disable_task",
                "time": t.created_at.isoformat(),
                "incident_key": t.open_state_token,
                "current_incident": bool(
                    current_incident_key and t.open_state_token == current_incident_key
                ),
                "status": t.status.value,
                "attempt_count": t.attempt_count,
                "requested_by": t.requested_by_username,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                "last_error": t.last_error,
            }
        )
    for event in recommendation_events:
        reason_title, reason_text = _normalize_enable_recommendation_reason(
            recommendation_level=event.recommendation_level,
            reason_title=event.reason_title,
            reason_text=event.reason_text,
        )
        timeline.append(
            {
                "type": "enable_recommendation",
                "time": event.created_at.isoformat(),
                "recommendation_level": event.recommendation_level.value,
                "delivery_status": event.delivery_status,
                "matched_rule_codes": event.matched_rule_codes or [],
                "reason_title": reason_title,
                "reason_text": reason_text,
                "metrics_json": event.metrics_json or {},
            }
        )
    for task in enable_tasks:
        timeline.append(
            {
                "type": "enable_task",
                "time": task.created_at.isoformat(),
                "status": task.status.value,
                "attempt_count": task.attempt_count,
                "requested_by": task.requested_by_username,
                "recommendation_event_id": (
                    str(task.recommendation_event_id) if task.recommendation_event_id else None
                ),
                "completed_at": task.completed_at.isoformat() if task.completed_at else None,
                "last_error": task.last_error,
            }
        )

    # Показываем новые события сверху, чтобы таймлайн читался как журнал.
    timeline.sort(key=lambda x: x["time"], reverse=True)

    return {
        "fb_ad_id": fb_ad_id,
        "ad_name": snapshot.ad_name if snapshot else None,
        "campaign_name": snapshot.campaign_name if snapshot else None,
        "adset_name": snapshot.adset_name if snapshot else None,
        "current_state": snapshot.alert_state.value if snapshot else None,
        "delivery_status": snapshot.delivery_status if snapshot else None,
        "current_incident": current_incident.model_dump() if current_incident else None,
        "current_metrics": {
            "spend": str(snapshot.spend) if snapshot else None,
            "budget": getattr(snapshot, "budget", None) if snapshot else None,
            "reach": getattr(snapshot, "reach", None) if snapshot else None,
            "impressions": getattr(snapshot, "impressions", None) if snapshot else None,
            "clicks": snapshot.clicks if snapshot else None,
            "cpc": str(snapshot.cpc) if snapshot and snapshot.cpc is not None else None,
            "ctr": (
                str(snapshot.ctr)
                if snapshot and getattr(snapshot, "ctr", None) is not None
                else None
            ),
            "delivery_status": snapshot.delivery_status if snapshot else None,
            "outbound_clicks": snapshot.outbound_clicks if snapshot else None,
            "outbound_ctr": str(snapshot.outbound_ctr)
            if snapshot and snapshot.outbound_ctr is not None
            else None,
            "landing_page_views": snapshot.landing_page_views if snapshot else None,
            "cost_per_result": (
                str(snapshot.cost_per_result)
                if snapshot and getattr(snapshot, "cost_per_result", None) is not None
                else None
            ),
            "cost_per_landing_page_view": (
                str(snapshot.cost_per_landing_page_view)
                if snapshot and snapshot.cost_per_landing_page_view is not None
                else None
            ),
            "cpm": str(snapshot.cpm) if snapshot and snapshot.cpm is not None else None,
            "frequency": str(snapshot.frequency)
            if snapshot and snapshot.frequency is not None
            else None,
            "leads": snapshot.leads if snapshot else None,
            "cost_per_lead": str(snapshot.cost_per_lead)
            if snapshot and snapshot.cost_per_lead is not None
            else None,
            "registrations": snapshot.registrations if snapshot else None,
            "cost_per_registration": (
                str(snapshot.cost_per_registration)
                if snapshot and snapshot.cost_per_registration is not None
                else None
            ),
            "deposits": snapshot.deposits if snapshot else None,
        }
        if snapshot
        else None,
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
    """Немедленно перезапустить профиль Vision и попросить observer переподключиться."""
    result = await db.execute(
        select(VisionSettings).where(VisionSettings.singleton_key == "default")
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=400, detail="Настройки Vision ещё не сохранены")

    if not row.x_token_encrypted:
        raise HTTPException(status_code=400, detail="Vision X-Token не настроен")
    if not row.profile_id:
        raise HTTPException(status_code=400, detail="Не выбран профиль Vision")

    x_token = decrypt(row.x_token_encrypted)
    if not x_token:
        raise HTTPException(status_code=400, detail="Не удалось расшифровать Vision X-Token")

    old_observer_pid = await _stop_observer_process()
    client = VisionClient(x_token=x_token, base_url=row.api_url)
    profile_port: int | None = None
    new_observer_pid: int | None = None
    reconnect_error: HTTPException | None = None

    try:
        folder_id = await client.resolve_folder_id(row.profile_id)
        try:
            await client.stop_profile(folder_id, row.profile_id)
        except Exception:
            # Профиль мог уже быть остановлен, это не должно ломать повторный старт.
            pass

        stopped = await client.wait_until_profile_stopped(row.profile_id)
        if not stopped:
            raise RuntimeError(f"Vision не остановил профиль {row.profile_id} после команды stop")
        profile = await client.start_profile(folder_id, row.profile_id)
        profile_port = profile.port
        row.reconnect_requested = True
        await db.commit()
    except HTTPException as exc:
        reconnect_error = exc
    except Exception as exc:
        reconnect_error = HTTPException(
            status_code=502,
            detail=f"Не удалось перезапустить профиль Vision: {exc}",
        )
    finally:
        await client.close()
        new_observer_pid = await _start_observer_process(
            reason="Ручное переподключение Vision через UI"
        )

    if reconnect_error is not None:
        raise reconnect_error

    if profile_port is not None:
        return {
            "ok": True,
            "message": (
                "Observer был временно остановлен, профиль Vision перезапущен, "
                "воркер запущен заново."
            ),
            "port": profile_port,
            "old_observer_pid": old_observer_pid,
            "new_observer_pid": new_observer_pid,
        }

    return {
        "ok": True,
        "message": (
            "Observer был перезапущен, профиль Vision тоже перезапущен, "
            "но CDP-порт пока не появился."
        ),
        "old_observer_pid": old_observer_pid,
        "new_observer_pid": new_observer_pid,
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
            profiles.append(
                {
                    "folder_id": item.get("folder_id", ""),
                    "profile_id": item.get("profile_id", ""),
                    "name": item.get("name") or item.get("profile_id", ""),
                    "port": item.get("port"),
                }
            )
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
    """Список дополнительных получателей Telegram-уведомлений."""
    result = await db.execute(
        select(TelegramRecipient).order_by(TelegramRecipient.created_at.asc())
    )
    recipients = result.scalars().all()
    return [
        TelegramRecipientSchema(
            id=str(r.id),
            chat_id=r.chat_id,
            masked_chat_id=mask_chat_id(r.chat_id),
            telegram_user_id=r.telegram_user_id,
            username=r.username,
            first_name=r.first_name,
            role=r.role or TelegramUserRole.RECIPIENT.value,
            is_active=r.is_active,
            created_at=r.created_at.isoformat(),
        )
        for r in recipients
    ]


@app.delete("/api/settings/telegram/recipients/{recipient_id}")
async def delete_telegram_recipient(recipient_id: str, db: AsyncSession = Depends(get_db)):
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
    row = await db.scalar(
        select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
    )
    if row is None or not row.is_authorized:
        raise HTTPException(status_code=400, detail="Telegram-бот не настроен")
    if not is_forum_delivery_mode(getattr(row, "delivery_mode", None)):
        raise HTTPException(
            status_code=400, detail="Инвайты доступны только после cutover в группу"
        )
    if not forum_topics_ready(row):
        raise HTTPException(status_code=400, detail="Forum topics ещё не готовы")

    invite = await create_telegram_invite(
        db,
        role=TelegramUserRole.RECIPIENT.value,
        created_by_telegram_user_id=row.owner_telegram_user_id or "",
        created_by_username=row.owner_username or "",
    )
    await db.commit()

    return InviteCodeResponse(
        code=invite.code,
        bot_username=row.bot_username or "",
        role=invite.role or TelegramUserRole.RECIPIENT.value,
        expires_at=invite.expires_at.isoformat() if invite.expires_at else None,
        deep_link="",
        activation_command=_activation_command(invite.code),
        activation_target=CONTROL_TOPIC_NAME,
    )

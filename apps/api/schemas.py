# -*- coding: utf-8 -*-
"""Pydantic-схемы для API запросов и ответов."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from core.domain import EnableRecommendationLevel, TelegramDeliveryMode, TelegramUserRole
from core.enable_recommendations.service import (
    OK_RECOMMENDATION_REASON_TEXT,
    OK_RECOMMENDATION_REASON_TITLE,
    EnableRecommendationCandidate,
)
from core.models import AdSnapshot, EnableRecommendationEvent
from core.telegram.service import CONTROL_TOPIC_NAME


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


# ==========================================
# Настройки
# ==========================================


class ObserverSettingsSchema(BaseModel):
    """Настройки observer (интервал из UI)."""

    interval_seconds: int = Field(default=90, ge=10, le=3600)
    jitter_seconds: int = Field(default=10, ge=0, le=300)
    warning_percent_of_stop: Decimal = Field(default=Decimal("80"), ge=0, le=100)
    stop_percent_of_base: Decimal = Field(default=Decimal("100"), gt=0)
    cpc_warning_percent_of_stop: Decimal | None = Field(default=None, ge=0, le=100)
    cpc_stop_percent_of_base: Decimal | None = Field(default=None, gt=0)
    cpl_warning_percent_of_stop: Decimal | None = Field(default=None, ge=0, le=100)
    cpl_stop_percent_of_base: Decimal | None = Field(default=None, gt=0)
    cpr_warning_percent_of_stop: Decimal | None = Field(default=None, ge=0, le=100)
    cpr_stop_percent_of_base: Decimal | None = Field(default=None, gt=0)
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


# ==========================================
# Офферы
# ==========================================


class OfferSchema(BaseModel):
    """Оффер с CPA. code = название оффера."""

    id: str | None = None
    code: str
    cpa_amount: Decimal
    payout_per_deposit: Decimal | None = None
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
    frequency_elevated_threshold: Decimal = Decimal("2")
    frequency_critical_threshold: Decimal = Decimal("3")


# ==========================================
# Dashboard и Снимки
# ==========================================


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
    cost_per_result: Decimal | None = None
    cpm: Decimal | None = None
    frequency: Decimal | None = None
    leads: int
    cost_per_lead: Decimal | None = None
    registrations: int
    cost_per_registration: Decimal | None = None
    deposits: int
    fake_deposits: int = 0
    effective_deposits: int = 0
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
    cost_per_deposit: Decimal | None = None
    roas: Decimal | None = None
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
    cost_per_deposit: Decimal | None = None
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
    db: str = "ok"


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
# История заливов
# ==========================================


class HistorySummarySchema(BaseModel):
    """Агрегированные метрики за период."""

    date_from: str
    date_to: str
    days_count: int
    total_spend: Decimal
    total_clicks: int
    total_leads: int
    total_registrations: int
    total_deposits: int
    avg_cpc: Decimal | None = None
    avg_cpl: Decimal | None = None
    avg_cpr: Decimal | None = None
    avg_cost_per_deposit: Decimal | None = None
    roas: Decimal | None = None
    total_alerts: int = 0
    total_stops: int = 0
    total_disables: int = 0
    # Метрики предыдущего периода для сравнения
    prev_spend: Decimal | None = None
    prev_leads: int | None = None
    prev_registrations: int | None = None
    prev_deposits: int | None = None


class HistoryTimelinePoint(BaseModel):
    """Точка графика трендов по дням."""

    date: str
    spend: Decimal = Decimal("0")
    clicks: int = 0
    leads: int = 0
    registrations: int = 0
    deposits: int = 0
    cpl: Decimal | None = None
    cpr: Decimal | None = None
    cpc: Decimal | None = None
    cost_per_deposit: Decimal | None = None


class HistoryCampaignRow(BaseModel):
    """Строка таблицы кампаний за период."""

    campaign_name: str
    offer_code: str | None = None
    total_spend: Decimal = Decimal("0")
    total_clicks: int = 0
    total_leads: int = 0
    total_registrations: int = 0
    total_deposits: int = 0
    avg_cpl: Decimal | None = None
    avg_cpr: Decimal | None = None
    avg_cost_per_deposit: Decimal | None = None
    roas: Decimal | None = None
    alerts_count: int = 0
    disables_count: int = 0


class HistoryOfferSummary(BaseModel):
    """Сводка по офферу за период."""

    offer_code: str
    total_spend: Decimal = Decimal("0")
    total_deposits: int = 0
    total_registrations: int = 0
    avg_cpr: Decimal | None = None
    avg_cost_per_deposit: Decimal | None = None
    roas: Decimal | None = None
    profit: Decimal | None = None
    alerts_count: int = 0
    disables_count: int = 0


class HistoryEventItem(BaseModel):
    """Элемент ленты событий."""

    id: str
    event_type: str  # "alert" | "disable" | "enable"
    fb_ad_id: str
    ad_name: str
    offer_code: str | None = None
    summary: str
    stage: str | None = None
    matched_rule_codes: list[str] = []
    status: str | None = None
    created_at: str


# === Per-ad история ===


class HistoryAdRow(BaseModel):
    """Строка таблицы объявлений за период."""

    fb_ad_id: str
    ad_name: str
    campaign_name: str
    offer_code: str | None = None
    total_spend: Decimal
    total_clicks: int
    total_leads: int
    total_registrations: int
    total_deposits: int
    avg_cpc: Decimal | None = None
    avg_cpl: Decimal | None = None
    avg_cpr: Decimal | None = None
    avg_cost_per_deposit: Decimal | None = None


# === Корректировка ложных депозитов ===


class AdDepositCorrectionUpdateSchema(BaseModel):
    """Тело запроса на установку ложных депозитов."""

    fake_count: int = Field(ge=0, description="Количество ложных депозитов")
    note: str = Field(default="", max_length=500, description="Причина корректировки")


class AdDepositCorrectionSchema(BaseModel):
    """Ответ с корректировкой ложных депозитов."""

    id: str
    fb_ad_id: str
    fake_count: int
    note: str
    ad_name: str | None = None
    campaign_name: str | None = None
    created_at: str
    updated_at: str


class HistoryEventsPage(BaseModel):
    """Лента событий с пагинацией."""

    items: list[HistoryEventItem]
    total: int
    limit: int
    offset: int


# ==========================================
# Трекер нейминга объявлений
# ==========================================


class NamingPatternAdSchema(BaseModel):
    """Пример объявления в группе нейминга."""

    ad_name: str
    fb_ad_id: str
    last_observed_at: str | None = None


class NamingPatternGroupSchema(BaseModel):
    """Группа объявлений с одним префиксом нейминга."""

    prefix: str
    offer_code: str | None = None
    offer_name: str | None = None
    max_number: int
    total_count: int
    recent_ads: list[NamingPatternAdSchema] = []


class NamingTrackerResponseSchema(BaseModel):
    """Ответ трекера паттернов нейминга."""

    patterns: list[NamingPatternGroupSchema] = []
    total_patterns: int = 0

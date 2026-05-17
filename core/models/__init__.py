# -*- coding: utf-8 -*-
"""ORM-модели для stop-бота."""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import Base
from core.domain import (
    AlertStage,
    AlertState,
    CampaignCreatorTaskStatus,
    DisableTaskStatus,
    EnableRecommendationLevel,
    EnableTaskStatus,
    PlanRunStatus,
    TelegramNotificationStream,
    TelegramUserRole,
)

# --- Общие миксины ---


class UUIDPrimaryKeyMixin:
    """UUID первичный ключ."""

    id: Mapped[_uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid.uuid4)


def _utcnow() -> datetime:
    """Текущее UTC-время (не deprecated в Python 3.12+)."""
    return datetime.now(UTC)


class TimestampMixin:
    """Временные метки создания и обновления."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


# --- Enum типы для Postgres ---
_ALERT_STAGE_ENUM = Enum(
    AlertStage, name="alert_stage_enum", values_callable=lambda e: [i.value for i in e]
)
_ALERT_STATE_ENUM = Enum(
    AlertState, name="alert_state_enum", values_callable=lambda e: [i.value for i in e]
)
_DISABLE_STATUS_ENUM = Enum(
    DisableTaskStatus,
    name="disable_task_status_enum",
    values_callable=lambda e: [i.value for i in e],
)
_ENABLE_RECOMMENDATION_LEVEL_ENUM = Enum(
    EnableRecommendationLevel,
    name="enable_recommendation_level_enum",
    values_callable=lambda e: [i.value for i in e],
)
_TELEGRAM_NOTIFICATION_STREAM_ENUM = Enum(
    TelegramNotificationStream,
    name="telegram_notification_stream_enum",
    values_callable=lambda e: [i.value for i in e],
)


# === Настройки Observer ===


class ObserverSettings(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Настройки наблюдателя (интервал, jitter, процент предупреждения)."""

    __tablename__ = "observer_settings"

    singleton_key: Mapped[str] = mapped_column(String(32), unique=True, default="default")
    interval_seconds: Mapped[int] = mapped_column(Integer, default=90)
    jitter_seconds: Mapped[int] = mapped_column(Integer, default=10)
    # Флаг включения/выключения сканирования из UI
    is_scanning_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Флаг немедленного скана (устанавливается из UI, сбрасывается воркером)
    scan_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Граница текущих суток кабинета, определяемая по zero-scan в observer
    cabinet_day_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    # Runtime-статус observer worker для UI-диагностики.
    worker_status: Mapped[str | None] = mapped_column(String(32))
    worker_message: Mapped[str | None] = mapped_column(String(500))
    worker_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_last_error: Mapped[str | None] = mapped_column(String(500))
    worker_last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_scan_interval_seconds: Mapped[int | None] = mapped_column(Integer)
    current_scan_jitter_seconds: Mapped[int | None] = mapped_column(Integer)
    current_scan_threat_level: Mapped[str | None] = mapped_column(String(32))
    next_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Флаг автоматического включения объявлений по рекомендациям
    auto_enable_recommendations: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    # Идентификатор рекламного кабинета Facebook (для ссылок в Ads Manager)
    fb_account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Время до которого сканирование стоит на паузе (авто-resume по истечении)
    pause_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Глобальные настройки комиссий для расчёта profit
    install_cost: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), default=Decimal("0.02"), server_default="0.02"
    )
    agent_commission_percent: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), default=Decimal("3"), server_default="3"
    )


class CabinetDayArchive(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Архив агрегатов завершившихся суток кабинета."""

    __tablename__ = "cabinet_day_archives"

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reset_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ads_count: Mapped[int] = mapped_column(Integer, default=0)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    campaigns_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    offer_stats_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ads_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


# === Telegram-настройки ===


class TelegramSettings(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Настройки Telegram-бота с авторизацией."""

    __tablename__ = "telegram_settings"

    singleton_key: Mapped[str] = mapped_column(String(32), unique=True, default="default")
    # Токен хранится зашифрованным (Fernet)
    bot_token_encrypted: Mapped[str] = mapped_column(Text, default="")
    chat_id: Mapped[str] = mapped_column(String(64), default="")
    owner_telegram_user_id: Mapped[str] = mapped_column(String(64), default="")
    owner_username: Mapped[str] = mapped_column(String(128), default="")
    owner_first_name: Mapped[str] = mapped_column(String(128), default="")
    # Авторизация: пользователь должен отправить /start боту
    is_authorized: Mapped[bool] = mapped_column(Boolean, default=False)
    # Одноразовый код для привязки (6 цифр)
    auth_code: Mapped[str] = mapped_column(String(16), default="")
    # Имя бота (кэшируем после getMe)
    bot_username: Mapped[str] = mapped_column(String(128), default="")
    # Пульс poller-а для диагностики состояния Telegram-контура
    poller_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Последний обработанный offset Telegram long polling
    poller_offset: Mapped[int | None] = mapped_column(Integer)
    # URL мини-приложения (Web App) для inline-кнопки.
    web_app_url: Mapped[str | None] = mapped_column(String(512))


# === Оффер ===


class Offer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Оффер с CPA и конфигурацией правил. code = название оффера."""

    __tablename__ = "offers"

    code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    cpa_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    payout_per_deposit: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False, server_default="0"
    )
    country_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Поля для автосоздания кампании
    landing_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    cabinet_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pixel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    geo_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    geo_slot_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    rule_config: Mapped[OfferRuleConfig | None] = relationship(
        back_populates="offer", cascade="all, delete-orphan", uselist=False
    )
    campaigns: Mapped[list[FbCampaign]] = relationship(back_populates="offer")


# === Конфигурация правил для оффера ===


class OfferRuleConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Конфигурация стоп-правил для конкретного оффера."""

    __tablename__ = "offer_rule_configs"

    offer_id: Mapped[_uuid.UUID] = mapped_column(
        ForeignKey("offers.id", ondelete="CASCADE"), unique=True, index=True
    )
    # Правило 1: CPC > X% CPA
    cpc_percent_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cpc_percent_stop: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("2"))

    # Правило 2: CPL > X% CPA
    cpl_percent_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cpl_percent_stop: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("10"))

    # Правило 3: CPR > X% CPA
    cpr_percent_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cpr_percent_stop: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("20"))

    # Правило 4: N рег без депов
    regs_no_dep_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    regs_no_dep_stop_count: Mapped[int] = mapped_column(Integer, default=5)

    # Правило 5: Расход 50-70% CPA, 0 депов, нормальная рега
    spend_no_dep_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    spend_no_dep_from_percent: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("50"))
    spend_no_dep_to_percent: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("70"))

    # Правило 6: Есть деп, расход 70-90% CPA
    spend_with_dep_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    spend_with_dep_from_percent: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), default=Decimal("70")
    )
    spend_with_dep_to_percent: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("90"))

    # Диагностика частоты
    frequency_elevated_threshold: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), default=Decimal("2")
    )
    frequency_critical_threshold: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), default=Decimal("3")
    )

    # Пороги warning/stop для этого оффера.
    warning_percent_of_stop: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("80"))
    stop_percent_of_base: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("80"))
    cpc_warning_percent_of_stop: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), default=Decimal("80")
    )
    cpc_stop_percent_of_base: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("80"))
    cpl_warning_percent_of_stop: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), default=Decimal("80")
    )
    cpl_stop_percent_of_base: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("80"))
    cpr_warning_percent_of_stop: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), default=Decimal("80")
    )
    cpr_stop_percent_of_base: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("80"))

    offer: Mapped[Offer] = relationship(back_populates="rule_config")


# === Справочник кампаний ===


class FbCampaign(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Кампания Facebook — группирует адсеты, привязывает оффер."""

    __tablename__ = "fb_campaigns"
    __table_args__ = (
        Index("uq_fb_campaign_name", "campaign_name", unique=True),
        Index("ix_fb_campaign_offer_id", "offer_id"),
    )

    campaign_name: Mapped[str] = mapped_column(String(255))
    offer_id: Mapped[_uuid.UUID | None] = mapped_column(
        ForeignKey("offers.id", ondelete="SET NULL"),
    )
    offer_code: Mapped[str | None] = mapped_column(String(100))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    offer: Mapped[Offer | None] = relationship(back_populates="campaigns")
    adsets: Mapped[list[FbAdset]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )


# === Справочник адсетов ===


class FbAdset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Адсет Facebook — группирует объявления внутри кампании."""

    __tablename__ = "fb_adsets"
    __table_args__ = (
        Index("uq_fb_adset_campaign_name", "campaign_id", "adset_name", unique=True),
        Index("ix_fb_adset_campaign_id", "campaign_id"),
    )

    adset_name: Mapped[str] = mapped_column(String(255))
    campaign_id: Mapped[_uuid.UUID] = mapped_column(
        ForeignKey("fb_campaigns.id", ondelete="CASCADE"),
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    campaign: Mapped[FbCampaign] = relationship(back_populates="adsets")
    ads: Mapped[list[FbAd]] = relationship(back_populates="adset", cascade="all, delete-orphan")


# === Справочник объявлений ===


class FbAd(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Каноническая запись объявления Facebook — единая точка правды для FK-связей.

    Оффер наследуется от кампании: fb_ads → fb_adsets → fb_campaigns → offers.
    """

    __tablename__ = "fb_ads"
    __table_args__ = (
        Index("uq_fb_ad_fb_ad_id", "fb_ad_id", unique=True),
        Index("ix_fb_ad_adset_id", "adset_id"),
        Index("ix_fb_ad_last_seen_at", "last_seen_at"),
    )

    fb_ad_id: Mapped[str] = mapped_column(String(32))
    ad_name: Mapped[str] = mapped_column(String(255), default="")
    adset_id: Mapped[_uuid.UUID] = mapped_column(
        ForeignKey("fb_adsets.id", ondelete="CASCADE"),
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    adset: Mapped[FbAdset] = relationship(back_populates="ads")
    snapshot: Mapped[AdSnapshot | None] = relationship(back_populates="fb_ad")
    metric_history: Mapped[list[AdMetricHistory]] = relationship(
        back_populates="fb_ad", cascade="all, delete-orphan"
    )
    alert_events: Mapped[list[AlertEvent]] = relationship(back_populates="fb_ad")
    disable_tasks: Mapped[list[DisableTask]] = relationship(back_populates="fb_ad")
    enable_recommendation_events: Mapped[list[EnableRecommendationEvent]] = relationship(
        back_populates="fb_ad"
    )
    enable_tasks: Mapped[list[EnableTask]] = relationship(back_populates="fb_ad")


# === История метрик объявления ===


class AdMetricHistory(UUIDPrimaryKeyMixin, Base):
    """Гранулярная история метрик — запись только при изменении значений."""

    __tablename__ = "ad_metric_history"
    __table_args__ = (
        UniqueConstraint("ad_id", "cycle_ts", name="uq_ad_metric_history_ad_ts"),
        Index("ix_ad_metric_history_cycle_ts", "cycle_ts"),
    )

    ad_id: Mapped[_uuid.UUID] = mapped_column(
        ForeignKey("fb_ads.id", ondelete="CASCADE"), index=True
    )
    cycle_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Метрики (зеркало AdSnapshot)
    spend: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    reach: Mapped[int] = mapped_column(Integer, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    cpc: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    ctr: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    cost_per_result: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    cpm: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    frequency: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    leads: Mapped[int] = mapped_column(Integer, default=0)
    cost_per_lead: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    registrations: Mapped[int] = mapped_column(Integer, default=0)
    cost_per_registration: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    deposits: Mapped[int] = mapped_column(Integer, default=0)
    outbound_clicks: Mapped[int] = mapped_column(Integer, default=0)
    outbound_ctr: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    landing_page_views: Mapped[int] = mapped_column(Integer, default=0)
    cost_per_landing_page_view: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))

    fb_ad: Mapped[FbAd] = relationship(back_populates="metric_history")


# === Снимок метрик объявления ===


class AdSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Текущий снимок метрик одного объявления.

    Идентификационные данные (campaign, adset, ad_name, offer) берутся через
    JOIN: ad_snapshots → fb_ads → fb_adsets → fb_campaigns → offers.
    """

    __tablename__ = "ad_snapshots"
    __table_args__ = (
        Index("uq_ad_snapshot_fb_ad", "fb_ad_id", unique=True),
        Index("ix_ad_snapshot_alert_state", "alert_state"),
        Index("ix_ad_snapshot_last_observed", "last_observed_at", "alert_state"),
        Index("ix_ad_snapshot_ad_id", "ad_id"),
    )

    ad_id: Mapped[_uuid.UUID] = mapped_column(
        ForeignKey("fb_ads.id", ondelete="CASCADE"), index=True
    )
    fb_ad_id: Mapped[str] = mapped_column(String(32))
    delivery_status: Mapped[str] = mapped_column(String(64))

    # Метрики
    spend: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    budget: Mapped[str] = mapped_column(String(255), default="")
    reach: Mapped[int] = mapped_column(Integer, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    cpc: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    ctr: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    cost_per_result: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    cpm: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    frequency: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    leads: Mapped[int] = mapped_column(Integer, default=0)
    cost_per_lead: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    registrations: Mapped[int] = mapped_column(Integer, default=0)
    cost_per_registration: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    deposits: Mapped[int] = mapped_column(Integer, default=0)
    outbound_clicks: Mapped[int] = mapped_column(Integer, default=0)
    outbound_ctr: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    landing_page_views: Mapped[int] = mapped_column(Integer, default=0)
    cost_per_landing_page_view: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))

    # Состояние алертов
    warning_rule_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    stop_rule_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    current_stage: Mapped[AlertStage | None] = mapped_column(_ALERT_STAGE_ENUM)
    alert_state: Mapped[AlertState] = mapped_column(_ALERT_STATE_ENUM, default=AlertState.NORMAL)
    open_state_token: Mapped[str | None] = mapped_column(String(64), index=True)

    # Telegram-привязка
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64))
    telegram_message_id: Mapped[int | None] = mapped_column(Integer)
    telegram_group_key: Mapped[str | None] = mapped_column(String(64), index=True)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Снузер: если задан и не истёк, observer не шлёт повторный алерт
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    fb_ad: Mapped[FbAd | None] = relationship(back_populates="snapshot")
    alerts: Mapped[list[AlertEvent]] = relationship(back_populates="snapshot")
    disable_tasks: Mapped[list[DisableTask]] = relationship(back_populates="snapshot")
    enable_recommendation_events: Mapped[list[EnableRecommendationEvent]] = relationship(
        back_populates="snapshot"
    )


# === Событие алерта ===


class AlertEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Событие алерта (WARNING или STOP)."""

    __tablename__ = "alert_events"
    __table_args__ = (Index("ix_alert_event_created_at", "created_at"),)

    ad_id: Mapped[_uuid.UUID] = mapped_column(
        ForeignKey("fb_ads.id", ondelete="CASCADE"), index=True
    )
    snapshot_id: Mapped[_uuid.UUID | None] = mapped_column(
        ForeignKey("ad_snapshots.id", ondelete="SET NULL"), index=True
    )
    offer_id: Mapped[_uuid.UUID | None] = mapped_column(
        ForeignKey("offers.id", ondelete="SET NULL"), index=True
    )
    stage: Mapped[AlertStage] = mapped_column(_ALERT_STAGE_ENUM, index=True)
    state: Mapped[AlertState] = mapped_column(_ALERT_STATE_ENUM, index=True)
    matched_rule_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    reason_title: Mapped[str | None] = mapped_column(String(255))
    reason_text: Mapped[str | None] = mapped_column(Text)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    message_text: Mapped[str | None] = mapped_column(Text)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64))
    telegram_message_id: Mapped[int | None] = mapped_column(Integer, index=True)
    telegram_group_key: Mapped[str | None] = mapped_column(String(64), index=True)

    fb_ad: Mapped[FbAd | None] = relationship(back_populates="alert_events")
    snapshot: Mapped[AdSnapshot | None] = relationship(back_populates="alerts")


# === Задача на отключение ===


class DisableTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Задача на отключение объявления (outbox-паттерн)."""

    __tablename__ = "disable_tasks"
    __table_args__ = (
        Index("uq_disable_task_idempotency", "idempotency_key", unique=True),
        # Очередь воркера: поиск задач по статусу и времени следующей попытки
        Index("ix_disable_task_queue", "status", "next_retry_at"),
        # Сверка по инциденту: поиск задач конкретного объявления в рамках токена
        Index("ix_disable_task_ad_incident", "ad_id", "open_state_token"),
        # Dashboard: фильтрация по времени завершения
        Index("ix_disable_task_completed_at", "completed_at"),
    )

    ad_id: Mapped[_uuid.UUID] = mapped_column(
        ForeignKey("fb_ads.id", ondelete="CASCADE"), index=True
    )
    snapshot_id: Mapped[_uuid.UUID | None] = mapped_column(
        ForeignKey("ad_snapshots.id", ondelete="SET NULL"), index=True
    )
    offer_id: Mapped[_uuid.UUID | None] = mapped_column(
        ForeignKey("offers.id", ondelete="SET NULL"), index=True
    )
    open_state_token: Mapped[str] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[DisableTaskStatus] = mapped_column(
        _DISABLE_STATUS_ENUM, default=DisableTaskStatus.PENDING, index=True
    )

    # Кто запросил
    requested_by_telegram_user_id: Mapped[str | None] = mapped_column(String(64))
    requested_by_username: Mapped[str | None] = mapped_column(String(255))

    # Retry-логика
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=10)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_error: Mapped[str | None] = mapped_column(String(500))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    fb_ad: Mapped[FbAd | None] = relationship(back_populates="disable_tasks")
    snapshot: Mapped[AdSnapshot | None] = relationship(back_populates="disable_tasks")

    @property
    def fb_ad_id(self) -> str | None:
        """Возвращает fb_ad_id из связанного объявления для обратной совместимости."""
        return self.fb_ad.fb_ad_id if self.fb_ad else None


# === Событие рекомендации на включение ===


class EnableRecommendationEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Событие рекомендации на включение объявления."""

    __tablename__ = "enable_recommendation_events"
    __table_args__ = (
        Index("uq_enable_recommendation_event_idempotency", "idempotency_key", unique=True),
    )

    ad_id: Mapped[_uuid.UUID] = mapped_column(
        ForeignKey("fb_ads.id", ondelete="CASCADE"), index=True
    )
    snapshot_id: Mapped[_uuid.UUID | None] = mapped_column(
        ForeignKey("ad_snapshots.id", ondelete="SET NULL"), index=True
    )
    offer_id: Mapped[_uuid.UUID | None] = mapped_column(
        ForeignKey("offers.id", ondelete="SET NULL"), index=True
    )
    delivery_status: Mapped[str] = mapped_column(String(64))
    recommendation_level: Mapped[EnableRecommendationLevel] = mapped_column(
        _ENABLE_RECOMMENDATION_LEVEL_ENUM,
        index=True,
    )
    matched_rule_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    reason_title: Mapped[str | None] = mapped_column(String(255))
    reason_text: Mapped[str | None] = mapped_column(Text)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    live_batch_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64))
    telegram_message_id: Mapped[int | None] = mapped_column(Integer, index=True)

    fb_ad: Mapped[FbAd] = relationship(back_populates="enable_recommendation_events")
    snapshot: Mapped[AdSnapshot | None] = relationship(
        back_populates="enable_recommendation_events"
    )


# === Задача на включение ===

_ENABLE_STATUS_ENUM = Enum(
    EnableTaskStatus,
    name="enable_task_status_enum",
    values_callable=lambda e: [i.value for i in e],
)


class AdDepositCorrection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Корректировка ложных депозитов по объявлению."""

    __tablename__ = "ad_deposit_corrections"
    __table_args__ = (Index("uq_ad_deposit_correction_ad_id", "ad_id", unique=True),)

    ad_id: Mapped[_uuid.UUID] = mapped_column(
        ForeignKey("fb_ads.id", ondelete="CASCADE"), index=True
    )
    fake_count: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(String(500), default="")


class EnableTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Задача на включение объявления (outbox-паттерн)."""

    __tablename__ = "enable_tasks"
    __table_args__ = (
        Index("uq_enable_task_idempotency", "idempotency_key", unique=True),
        # Очередь воркера: поиск задач по статусу и времени следующей попытки
        Index("ix_enable_task_queue", "status", "next_retry_at"),
    )

    ad_id: Mapped[_uuid.UUID] = mapped_column(
        ForeignKey("fb_ads.id", ondelete="CASCADE"), index=True
    )
    snapshot_id: Mapped[_uuid.UUID | None] = mapped_column(
        ForeignKey("ad_snapshots.id", ondelete="SET NULL"), index=True
    )
    recommendation_event_id: Mapped[_uuid.UUID | None] = mapped_column(
        ForeignKey("enable_recommendation_events.id", ondelete="SET NULL"),
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[EnableTaskStatus] = mapped_column(
        _ENABLE_STATUS_ENUM, default=EnableTaskStatus.PENDING, index=True
    )

    # Кто запросил
    requested_by_telegram_user_id: Mapped[str | None] = mapped_column(String(64))
    requested_by_username: Mapped[str | None] = mapped_column(String(255))

    # Retry-логика
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=10)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_error: Mapped[str | None] = mapped_column(String(500))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    fb_ad: Mapped[FbAd | None] = relationship(back_populates="enable_tasks")
    recommendation_event: Mapped[EnableRecommendationEvent | None] = relationship()


# === Per-ad флаг отключения автовключения ===


class AdAutoEnableDisabled(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Хранит fb_ad_id объявлений, у которых автовключение ВЫКЛЮЧЕНО вручную.

    При смене cabinet_day_started_at устаревшие записи удаляются воркером.
    """

    __tablename__ = "ad_auto_enable_disabled"

    fb_ad_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    cabinet_day_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# === Настройки Vision браузера ===


class VisionSettings(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Настройки подключения к Vision anti-detect браузеру."""

    __tablename__ = "vision_settings"

    singleton_key: Mapped[str] = mapped_column(String(32), unique=True, default="default")
    api_url: Mapped[str] = mapped_column(String(255), default="http://127.0.0.1:3030")
    # Токен хранится зашифрованным (Fernet)
    x_token_encrypted: Mapped[str] = mapped_column(Text, default="")
    profile_id: Mapped[str] = mapped_column(String(128), default="")
    column_widths_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    # Флаг для observer: переподключиться к браузеру при следующем цикле
    reconnect_requested: Mapped[bool] = mapped_column(Boolean, default=False)


# === Telegram инвайты и получатели ===


class TelegramInvite(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Одноразовый инвайт-код для подключения Telegram-получателя."""

    __tablename__ = "telegram_invites"

    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(32), default=TelegramUserRole.RECIPIENT.value)
    created_by_telegram_user_id: Mapped[str] = mapped_column(String(64), default="")
    created_by_username: Mapped[str] = mapped_column(String(255), default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class TelegramRecipient(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Авторизованный получатель Telegram-уведомлений."""

    __tablename__ = "telegram_recipients"
    __table_args__ = (
        Index(
            "uq_telegram_recipients_chat_and_user",
            "chat_id",
            "telegram_user_id",
            unique=True,
        ),
    )

    chat_id: Mapped[str] = mapped_column(String(64), index=True)
    telegram_user_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    username: Mapped[str] = mapped_column(String(128), default="")
    first_name: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[str] = mapped_column(String(32), default=TelegramUserRole.RECIPIENT.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class TelegramMessageRef(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Последний delivery-ref Telegram-сообщения для конкретного потока уведомлений."""

    __tablename__ = "telegram_message_refs"
    __table_args__ = (
        Index(
            "uq_telegram_message_refs_stream",
            "telegram_chat_id",
            "ad_id",
            "incident_key",
            "stream_kind",
            unique=True,
        ),
    )

    ad_id: Mapped[_uuid.UUID] = mapped_column(
        ForeignKey("fb_ads.id", ondelete="CASCADE"), index=True
    )
    telegram_chat_id: Mapped[str] = mapped_column(String(64), index=True)
    telegram_message_id: Mapped[int] = mapped_column(Integer, index=True)
    incident_key: Mapped[str] = mapped_column(String(64), default="", index=True)
    stream_kind: Mapped[TelegramNotificationStream] = mapped_column(
        _TELEGRAM_NOTIFICATION_STREAM_ENUM,
        index=True,
    )


# === Heartbeat воркеров ===


class WorkerHeartbeat(Base):
    """Универсальная таблица heartbeat'ов всех воркеров системы.

    Каждый воркер записывает сюда свой пульс (upsert по worker_name).
    Используется health_watchdog и /api/health/details для проверки живости.
    """

    __tablename__ = "worker_heartbeats"

    worker_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


# === Снуз алертов ===


class AlertSnooze(UUIDPrimaryKeyMixin, Base):
    """Запись о временном снузе алерта для конкретного объявления."""

    __tablename__ = "alert_snoozes"
    __table_args__ = (
        Index("ix_alert_snoozes_fb_ad_id", "fb_ad_id"),
        Index("ix_alert_snoozes_snoozed_until", "snoozed_until"),
    )

    fb_ad_id: Mapped[str] = mapped_column(String(32), nullable=False)
    snoozed_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_telegram_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


# === Задача автоматического создания кампании ===

_CAMPAIGN_CREATOR_STATUS_ENUM = Enum(
    CampaignCreatorTaskStatus,
    name="campaign_creator_task_status_enum",
    values_callable=lambda e: [i.value for i in e],
)


class CampaignCreatorTask(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Задача автоматического создания кампании в Ads Manager."""

    __tablename__ = "campaign_creator_tasks"

    offer_code: Mapped[str] = mapped_column(String(64), nullable=False)
    creative_folder: Mapped[str] = mapped_column(String(256), nullable=False)
    cabinet_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[CampaignCreatorTaskStatus] = mapped_column(
        _CAMPAIGN_CREATOR_STATUS_ENUM,
        default=CampaignCreatorTaskStatus.PENDING,
        nullable=False,
    )
    current_step: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checkpoint_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    context_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    spec_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    plan_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    progress_index: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    fb_state_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_error_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    campaign_name: Mapped[str | None] = mapped_column(String(256), nullable=True)


# === Creator v2: Plan / PlanRun ===

_PLAN_RUN_STATUS_ENUM = Enum(
    PlanRunStatus,
    name="plan_run_status_enum",
    values_callable=lambda e: [i.value for i in e],
)


class Plan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Декларативный план создания FB-кампании (creator v2)."""

    __tablename__ = "creator_plans"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PlanRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Запуск плана на конкретном профиле (creator v2)."""

    __tablename__ = "creator_plan_runs"

    plan_id: Mapped[_uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("creator_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    profile_id: Mapped[str] = mapped_column(String(128), nullable=False)
    variables: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[PlanRunStatus] = mapped_column(
        _PLAN_RUN_STATUS_ENUM, nullable=False, default=PlanRunStatus.QUEUED, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    step_log: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

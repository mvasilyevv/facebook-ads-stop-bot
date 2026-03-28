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
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import Base
from core.domain import AlertStage, AlertState, DisableTaskStatus, EnableTaskStatus

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


# === Настройки Observer ===


class ObserverSettings(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Настройки наблюдателя (интервал, jitter, процент предупреждения)."""

    __tablename__ = "observer_settings"

    singleton_key: Mapped[str] = mapped_column(String(32), unique=True, default="default")
    interval_seconds: Mapped[int] = mapped_column(Integer, default=90)
    jitter_seconds: Mapped[int] = mapped_column(Integer, default=10)
    warning_percent_of_stop: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("80"))
    # Глобальный коэффициент досрочного стопа для CPA-правил. 100 = без смещения.
    stop_percent_of_base: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("100"))
    # Флаг включения/выключения сканирования из UI
    is_scanning_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Флаг немедленного скана (устанавливается из UI, сбрасывается воркером)
    scan_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Граница текущих суток кабинета, определяемая по zero-scan в observer
    cabinet_day_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class CabinetDayArchive(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Архив агрегатов завершившихся суток кабинета."""

    __tablename__ = "cabinet_day_archives"

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reset_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ads_count: Mapped[int] = mapped_column(Integer, default=0)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    campaigns_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


# === Telegram-настройки ===


class TelegramSettings(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Настройки Telegram-бота с авторизацией."""

    __tablename__ = "telegram_settings"

    singleton_key: Mapped[str] = mapped_column(String(32), unique=True, default="default")
    # Токен хранится зашифрованным (Fernet)
    bot_token_encrypted: Mapped[str] = mapped_column(Text, default="")
    chat_id: Mapped[str] = mapped_column(String(64), default="")
    # Авторизация: пользователь должен отправить /start боту
    is_authorized: Mapped[bool] = mapped_column(Boolean, default=False)
    # Одноразовый код для привязки (6 цифр)
    auth_code: Mapped[str] = mapped_column(String(16), default="")
    # Имя бота (кэшируем после getMe)
    bot_username: Mapped[str] = mapped_column(String(128), default="")
    # Список одноразовых кодов для добавления новых получателей (JSON)
    pending_codes: Mapped[list] = mapped_column(JSON, default=list)


# === Оффер ===


class Offer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Оффер с CPA и конфигурацией правил."""

    __tablename__ = "offers"

    code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    cpa_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    rule_config: Mapped[OfferRuleConfig | None] = relationship(
        back_populates="offer", cascade="all, delete-orphan", uselist=False
    )
    snapshots: Mapped[list[AdSnapshot]] = relationship(back_populates="offer")


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

    offer: Mapped[Offer] = relationship(back_populates="rule_config")


# === Снимок метрик объявления ===


class AdSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Текущий снимок метрик одного объявления."""

    __tablename__ = "ad_snapshots"
    __table_args__ = (Index("uq_ad_snapshot_fb_ad", "fb_ad_id", unique=True),)

    offer_id: Mapped[_uuid.UUID | None] = mapped_column(
        ForeignKey("offers.id", ondelete="SET NULL"), index=True
    )
    fb_ad_id: Mapped[str] = mapped_column(String(32), index=True)
    campaign_name: Mapped[str] = mapped_column(String(255))
    adset_name: Mapped[str] = mapped_column(String(255))
    ad_name: Mapped[str] = mapped_column(String(255))
    delivery_status: Mapped[str] = mapped_column(String(64))
    resolved_offer_code: Mapped[str | None] = mapped_column(String(100), index=True)

    # Метрики
    spend: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    cpc: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    leads: Mapped[int] = mapped_column(Integer, default=0)
    cost_per_lead: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    registrations: Mapped[int] = mapped_column(Integer, default=0)
    cost_per_registration: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    deposits: Mapped[int] = mapped_column(Integer, default=0)

    # Состояние алертов
    warning_rule_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    stop_rule_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    current_stage: Mapped[AlertStage | None] = mapped_column(_ALERT_STAGE_ENUM)
    alert_state: Mapped[AlertState] = mapped_column(
        _ALERT_STATE_ENUM, default=AlertState.NORMAL, index=True
    )
    open_state_token: Mapped[str | None] = mapped_column(String(64), index=True)

    # Telegram-привязка
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64))
    telegram_message_id: Mapped[int | None] = mapped_column(Integer)
    telegram_group_key: Mapped[str | None] = mapped_column(String(64), index=True)
    last_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, default=_utcnow
    )
    # Снузер: если задан и не истёк, observer не шлёт повторный алерт
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    offer: Mapped[Offer | None] = relationship(back_populates="snapshots")
    alerts: Mapped[list[AlertEvent]] = relationship(back_populates="snapshot")
    disable_tasks: Mapped[list[DisableTask]] = relationship(back_populates="snapshot")


# === Событие алерта ===


class AlertEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Событие алерта (WARNING или STOP)."""

    __tablename__ = "alert_events"

    snapshot_id: Mapped[_uuid.UUID | None] = mapped_column(
        ForeignKey("ad_snapshots.id", ondelete="SET NULL"), index=True
    )
    offer_id: Mapped[_uuid.UUID | None] = mapped_column(
        ForeignKey("offers.id", ondelete="SET NULL"), index=True
    )
    fb_ad_id: Mapped[str] = mapped_column(String(32), index=True)
    ad_name: Mapped[str] = mapped_column(String(255))
    stage: Mapped[AlertStage] = mapped_column(_ALERT_STAGE_ENUM, index=True)
    state: Mapped[AlertState] = mapped_column(_ALERT_STATE_ENUM, index=True)
    matched_rule_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    message_text: Mapped[str | None] = mapped_column(Text)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64))
    telegram_message_id: Mapped[int | None] = mapped_column(Integer, index=True)
    telegram_group_key: Mapped[str | None] = mapped_column(String(64), index=True)

    snapshot: Mapped[AdSnapshot | None] = relationship(back_populates="alerts")


# === Задача на отключение ===


class DisableTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Задача на отключение объявления (outbox-паттерн)."""

    __tablename__ = "disable_tasks"
    __table_args__ = (Index("uq_disable_task_idempotency", "idempotency_key", unique=True),)

    snapshot_id: Mapped[_uuid.UUID | None] = mapped_column(
        ForeignKey("ad_snapshots.id", ondelete="SET NULL"), index=True
    )
    offer_id: Mapped[_uuid.UUID | None] = mapped_column(
        ForeignKey("offers.id", ondelete="SET NULL"), index=True
    )
    fb_ad_id: Mapped[str] = mapped_column(String(32), index=True)
    ad_name: Mapped[str] = mapped_column(String(255))
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

    snapshot: Mapped[AdSnapshot | None] = relationship(back_populates="disable_tasks")


# === Задача на включение ===

_ENABLE_STATUS_ENUM = Enum(
    EnableTaskStatus,
    name="enable_task_status_enum",
    values_callable=lambda e: [i.value for i in e],
)


class EnableTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Задача на включение объявления (outbox-паттерн)."""

    __tablename__ = "enable_tasks"
    __table_args__ = (Index("uq_enable_task_idempotency", "idempotency_key", unique=True),)

    snapshot_id: Mapped[_uuid.UUID | None] = mapped_column(
        ForeignKey("ad_snapshots.id", ondelete="SET NULL"), index=True
    )
    fb_ad_id: Mapped[str] = mapped_column(String(32), index=True)
    ad_name: Mapped[str] = mapped_column(String(255))
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


# === Настройки Vision браузера ===


class VisionSettings(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Настройки подключения к Vision anti-detect браузеру."""

    __tablename__ = "vision_settings"

    singleton_key: Mapped[str] = mapped_column(String(32), unique=True, default="default")
    api_url: Mapped[str] = mapped_column(String(255), default="http://127.0.0.1:3030")
    # Токен хранится зашифрованным (Fernet)
    x_token_encrypted: Mapped[str] = mapped_column(Text, default="")
    profile_id: Mapped[str] = mapped_column(String(128), default="")
    # Флаг для observer: переподключиться к браузеру при следующем цикле
    reconnect_requested: Mapped[bool] = mapped_column(Boolean, default=False)


# === Telegram получатели (мультипользователи) ===


class TelegramRecipient(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Авторизованный получатель Telegram-уведомлений."""

    __tablename__ = "telegram_recipients"

    chat_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(128), default="")
    first_name: Mapped[str] = mapped_column(String(128), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

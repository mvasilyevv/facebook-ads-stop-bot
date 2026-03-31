# -*- coding: utf-8 -*-
"""Enum-ы стадий алертов и состояний."""

from __future__ import annotations

from enum import StrEnum


class AlertStage(StrEnum):
    """Стадия алерта: ранний сигнал, предупреждение или стоп."""

    EARLY_SIGNAL = "EARLY_SIGNAL"
    WARNING = "WARNING"
    STOP = "STOP"


class AlertState(StrEnum):
    """Состояние объявления в конечном автомате."""

    NORMAL = "NORMAL"
    EARLY_SIGNAL_SENT = "EARLY_SIGNAL_SENT"
    WARNING_SENT = "WARNING_SENT"
    STOP_SENT = "STOP_SENT"
    CLAIMED = "CLAIMED"
    DISABLED = "DISABLED"


class DisableTaskStatus(StrEnum):
    """Статус задачи на отключение объявления."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    RETRYING = "RETRYING"
    SUCCEEDED = "SUCCEEDED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class EnableTaskStatus(StrEnum):
    """Статус задачи на включение объявления."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    RETRYING = "RETRYING"
    SUCCEEDED = "SUCCEEDED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class EnableRecommendationLevel(StrEnum):
    """Уровень рекомендации на включение объявления."""

    OK = "OK"
    EARLY_SIGNAL = "EARLY_SIGNAL"
    WARNING = "WARNING"


class TelegramUserRole(StrEnum):
    """Роль пользователя в Telegram-контуре."""

    OWNER = "owner"
    RECIPIENT = "recipient"


class TelegramDeliveryMode(StrEnum):
    """Режим доставки Telegram-контура."""

    PRIVATE_CHAT = "PRIVATE_CHAT"
    FORUM_GROUP = "FORUM_GROUP"


class TelegramNotificationStream(StrEnum):
    """Независимый поток Telegram-уведомлений внутри одного чата."""

    EARLY = "EARLY"
    WARNING = "WARNING"
    STOP = "STOP"
    ENABLE = "ENABLE"

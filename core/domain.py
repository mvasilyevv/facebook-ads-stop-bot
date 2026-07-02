# -*- coding: utf-8 -*-
"""Enum-ы стадий алертов и состояний."""

from __future__ import annotations

from enum import StrEnum


class AlertStage(StrEnum):
    """Стадия алерта: предупреждение или стоп."""

    WARNING = "WARNING"
    STOP = "STOP"


class AlertState(StrEnum):
    """Состояние объявления в конечном автомате."""

    NORMAL = "NORMAL"
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
    WARNING = "WARNING"


class TelegramUserRole(StrEnum):
    """Роль пользователя в Telegram-контуре."""

    OWNER = "owner"
    RECIPIENT = "recipient"


class TelegramNotificationStream(StrEnum):
    """Независимый поток Telegram-уведомлений внутри одного чата."""

    WARNING = "WARNING"
    STOP = "STOP"
    ENABLE = "ENABLE"
    OPS = "OPS"


class PlanRunStatus(StrEnum):
    """Статус запуска плана creator-агента."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    REQUIRES_ATTENTION = "requires_attention"

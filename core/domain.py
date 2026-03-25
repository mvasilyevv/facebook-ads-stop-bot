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

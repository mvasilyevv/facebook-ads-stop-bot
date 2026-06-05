# -*- coding: utf-8 -*-
"""Pydantic-схемы для роутера observer (схема БД)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ObserverStatusResponse(BaseModel):
    """Статус observer-воркера из Redis ключа observer:runtime.

    Если ключ отсутствует — возвращаются None/unknown значения.
    """

    model_config = ConfigDict(from_attributes=True)

    status: str = "unknown"
    last_scan_at: datetime | None = None
    last_scan_outcome: str | None = None
    scans_today: int = 0
    interval_seconds: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ScanRunRow(BaseModel):
    """Одна строка scan_runs (partitioned by started_at)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    scan_id: int
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    outcome: str | None = None
    alerts_warning: int | None = None
    alerts_stop: int | None = None
    metrics_inserted: int | None = None
    error_message: str | None = None


class ScanRunsResponse(BaseModel):
    """Ответ на GET /observer/scan-runs."""

    runs: list[ScanRunRow]
    total: int


class StartCabinetDayResponse(BaseModel):
    """Ответ на POST /observer/start-new-cabinet-day."""

    status: str
    archived_date: str


class RestartSignalResponse(BaseModel):
    """Ответ на POST /observer/restart."""

    status: str
    channel: str

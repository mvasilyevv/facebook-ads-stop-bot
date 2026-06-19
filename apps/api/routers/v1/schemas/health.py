# -*- coding: utf-8 -*-
"""Pydantic-схемы для роутера health_details."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkerStatus(BaseModel):
    """Статус одного воркера по Redis heartbeat."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    status: Literal["ONLINE", "OFFLINE"]
    last_heartbeat_at: datetime | None = None
    ttl_seconds: int | None = None
    payload: dict[str, Any] | None = None


class MetaApiChannelStatus(BaseModel):
    """Статус сетевого канала Marketing API (auto-stop) по проактивному probe.

    Пишется health_watchdog в Redis meta_api:channel:health. ONLINE — реальный GET /me
    прошёл; DEGRADED — канал мёртв (network-down / протух токен); UNKNOWN — прободер не
    писал / ключ протух (нет данных, НЕ обязательно отказ).
    """

    model_config = ConfigDict(from_attributes=True)

    status: Literal["ONLINE", "DEGRADED", "UNKNOWN"]
    healthy: bool | None = None
    probe_ok: bool | None = None
    detail: str | None = None
    reason: str | None = None
    checked_at: datetime | None = None


class HealthDetailsResponse(BaseModel):
    """Агрегированный статус всех воркеров из Redis heartbeat-ключей."""

    model_config = ConfigDict(from_attributes=True)

    workers: list[WorkerStatus]
    observer_runtime: dict[str, Any] | None = Field(
        default=None,
        description="Содержимое Redis ключа observer:runtime",
    )
    meta_api_channel: MetaApiChannelStatus | None = Field(
        default=None,
        description="Статус сетевого канала Marketing API (probe из health_watchdog)",
    )
    overall: Literal["HEALTHY", "DEGRADED", "CRITICAL"]

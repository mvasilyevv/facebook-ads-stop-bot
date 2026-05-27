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


class HealthDetailsResponse(BaseModel):
    """Агрегированный статус всех воркеров из Redis heartbeat-ключей."""

    model_config = ConfigDict(from_attributes=True)

    workers: list[WorkerStatus]
    observer_runtime: dict[str, Any] | None = Field(
        default=None,
        description="Содержимое Redis ключа observer:runtime",
    )
    overall: Literal["HEALTHY", "DEGRADED", "CRITICAL"]

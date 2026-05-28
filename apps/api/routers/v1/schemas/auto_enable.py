# -*- coding: utf-8 -*-
"""Pydantic-схемы для роутера auto_enable (CRUD /dashboard/auto-enable-disabled)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AutoEnableDisabledOut(BaseModel):
    """Ответ одной записи AdAutoEnableDisabled с JOIN на fb_ads."""

    model_config = ConfigDict(from_attributes=True)

    fb_ad_id: str
    internal_id: uuid.UUID
    ad_name: str | None = None
    disabled_at: datetime
    reason: str | None = None


class AutoEnableDisabledIn(BaseModel):
    """Тело POST /dashboard/auto-enable-disabled/{fb_ad_id}.

    reason — опциональный комментарий. Фронт не передаёт тело (disableAutoEnable без body),
    поэтому все поля опциональны.
    """

    reason: str | None = Field(
        None,
        max_length=64,
        description="Причина отключения авто-включения (опционально)",
    )

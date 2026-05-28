# -*- coding: utf-8 -*-
"""Pydantic-схемы для роутера fake_deposits (CRUD /fake-deposits)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FakeDepositOut(BaseModel):
    """Ответ одной записи AdDepositCorrection с JOIN на fb_ads."""

    model_config = ConfigDict(from_attributes=True)

    fb_ad_id: str
    internal_id: uuid.UUID
    ad_name: str | None = None
    # corrected_deposits хранится в БД, фронт ожидает fake_count
    fake_count: int
    note: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class FakeDepositUpsertIn(BaseModel):
    """Тело PUT /fake-deposits/{fb_ad_id}.

    fake_count >= 0 (отрицательные депозиты физически невозможны).
    """

    # Фронт передаёт fake_count, маппим на corrected_deposits в БД
    fake_count: int = Field(..., ge=0, description="Количество фейковых депозитов (>= 0)")
    note: str | None = Field(None, description="Комментарий к корректировке")

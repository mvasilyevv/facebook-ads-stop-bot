# -*- coding: utf-8 -*-
"""Флаг не включать автоматически per объявление."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, CreatedAtOnly, UUIDPrimaryKey


class AdAutoEnableDisabled(UUIDPrimaryKey, CreatedAtOnly, Base):
    """Метка «не включать это объявление автоматически» (против авто-recovery).

    Постоянная: ставится через POST /settings/observer/auto-enable-exclusions/{fb_ad_id},
    снимается ТОЛЬКО вручную через DELETE. enable_recommendation_worker использует
    таблицу как статичный NOT IN фильтр.

    M-12 (аудит 2026-07-12): исходный docstring обещал сброс при cabinet_day
    rollover, но такого кода нет НИГДЕ (проверено grep'ом по воркерам) — флаг
    живёт до ручного снятия. Контракт приведён в соответствие с реальностью;
    авто-сброс по дню НЕ реализован намеренно (объявление, отключённое от
    авто-recovery, не должно тихо возвращаться в него на следующий день).
    Поле cabinet_day_started_at сохранено как аудит-отметка момента установки.
    Retention: CASCADE через fb_ads.
    """

    __tablename__ = "ad_auto_enable_disabled"

    ad_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fb_ads.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Момент установки флага (аудит). НЕ триггер авто-сброса — см. docstring класса.
    cabinet_day_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("ad_id", name="uq_ad_auto_enable_disabled_ad"),
        Index("ix_ad_auto_disable_day", "cabinet_day_started_at"),
    )

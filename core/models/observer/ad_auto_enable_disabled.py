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
    """Метка не включать автоматически.

    Сбрасывается при cabinet_day rollover (управляется enable_recommendation_worker).
    Retention: CASCADE через fb_ads + DELETE при rollover.
    """

    __tablename__ = "ad_auto_enable_disabled"

    ad_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fb_ads.id", ondelete="CASCADE"),
        nullable=False,
    )
    cabinet_day_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("ad_id", name="uq_ad_auto_enable_disabled_ad"),
        Index("ix_ad_auto_disable_day", "cabinet_day_started_at"),
    )

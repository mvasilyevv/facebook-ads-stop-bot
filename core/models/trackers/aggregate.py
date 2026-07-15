# -*- coding: utf-8 -*-
"""Агрегаты конверсий per (ad_id, country, day) — для быстрого чтения."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, Timestamp, UUIDPrimaryKey


class TrackerAggregate(UUIDPrimaryKey, Timestamp, Base):
    """Агрегаты per (ad_id, country, day).

    UNIQUE(ad_id, country, day) — гарантия идемпотентности UPSERT.
    Rebuild-паттерн через tracker_aggregator.

    M-9 (аудит 2026-07-12): ad_id FK — ondelete=SET NULL, а не CASCADE. Hard-delete
    fb_ads раньше каскадом уничтожал агрегаты и делал их невосстановимыми (постбэки
    выживают через SET NULL, но aggregator скипает fb_ad_fk IS NULL → пересчитать
    нечем). Теперь revenue-история переживает удаление ада (строка с ad_id=NULL).
    """

    __tablename__ = "tracker_aggregate"

    # nullable + SET NULL: агрегат-история переживает hard-delete ада (M-9).
    ad_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fb_ads.id", ondelete="SET NULL"),
        nullable=True,
    )
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    installs: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    registrations: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    ftds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    deposits: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    confirmed_deposits: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    redeposits: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Numeric(12,4) — согласовано с точностью источника (adsetpro_postback_events
    # revenue приходит с 4 знаками после запятой); было (12,2) и округляло агрегат
    # до копеек, теряя 2 младших разряда постбэка (MID-15, migration 0032).
    revenue: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, server_default="0")
    roi_percent: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)
    last_postback_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("ad_id", "country", "day", name="uq_tracker_aggregate_ad_country_day"),
        Index("ix_tracker_agg_ad_day", "ad_id", "day"),
        Index("ix_tracker_agg_day", "day"),
    )

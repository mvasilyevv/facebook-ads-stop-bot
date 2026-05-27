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
    """

    __tablename__ = "tracker_aggregate"

    ad_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fb_ads.id", ondelete="CASCADE"),
        nullable=False,
    )
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    installs: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    registrations: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    deposits: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    revenue: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    roi_percent: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)
    last_postback_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("ad_id", "country", "day", name="uq_tracker_aggregate_ad_country_day"),
        Index("ix_tracker_agg_ad_day", "ad_id", "day"),
        Index("ix_tracker_agg_day", "day"),
    )

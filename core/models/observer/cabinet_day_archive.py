# -*- coding: utf-8 -*-
"""Ежедневный snapshot агрегатов по дню рекламного кабинета."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Index, Integer, Numeric, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, CreatedAtOnly, UUIDPrimaryKey


class CabinetDayArchive(UUIDPrimaryKey, CreatedAtOnly, Base):
    """Snapshot агрегатов за день рекламного кабинета.

    Retention: 365 дней.
    """

    __tablename__ = "cabinet_day_archives"

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reset_detected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    total_spend: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    total_deposits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_leads: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ad_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_aggregate: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        Index("ix_cabinet_archives_started", "started_at"),
        Index(
            "ix_cabinet_archives_ended",
            "ended_at",
            postgresql_where=text("ended_at IS NOT NULL"),
        ),
    )

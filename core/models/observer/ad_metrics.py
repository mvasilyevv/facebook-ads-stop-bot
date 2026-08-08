# -*- coding: utf-8 -*-
"""Метрики объявлений во времени — единственный источник (заменяет ad_snapshots + ad_metric_history).

PARTITIONED BY RANGE (cycle_ts). Retention 90 дней.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, CreatedAtOnly


class AdMetrics(CreatedAtOnly, Base):
    """Метрика объявления в конкретный момент scan'а.

    Текущее значение = LIMIT 1 ORDER BY cycle_ts DESC.
    18 метрических полей — все nullable (часть может отсутствовать у новых ads).
    """

    __tablename__ = "ad_metrics"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("gen_random_uuid()"),
    )
    ad_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fb_ads.id", ondelete="CASCADE"),
        nullable=False,
    )
    cycle_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scan_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    # 18 метрических полей
    # ISO 4217 includes reviewed zero-, two- and three-decimal currencies.
    # Currency-aware consumers reject NULL/mixed evidence instead of guessing.
    spend: Mapped[Decimal | None] = mapped_column(Numeric(18, 3), nullable=True)
    reach: Mapped[int | None] = mapped_column(Integer, nullable=True)
    impressions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clicks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cpc: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    ctr: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    cost_per_result: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    cpm: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    frequency: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    leads: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_per_lead: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    registrations: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_per_registration: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    deposits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outbound_clicks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outbound_ctr: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    landing_page_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_per_landing_page_view: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6), nullable=True
    )

    __table_args__ = (
        PrimaryKeyConstraint("id", "cycle_ts", name="pk_ad_metrics"),
        UniqueConstraint("ad_id", "cycle_ts", name="uq_ad_metrics_ad_cycle"),
        Index(
            "ix_ad_metrics_ad_cycle",
            "ad_id",
            "cycle_ts",
        ),
        Index(
            "ix_ad_metrics_scan",
            "scan_id",
            postgresql_where=text("scan_id IS NOT NULL"),
        ),
        CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'",
            name="currency",
        ),
        {"postgresql_partition_by": "RANGE (cycle_ts)"},
    )

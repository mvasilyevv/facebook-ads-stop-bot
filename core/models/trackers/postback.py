# -*- coding: utf-8 -*-
"""Raw postback'и от AdsetPro. Partitioned by month."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base


class TrackerPostback(Base):
    """Raw postback от AdsetPro.

    PARTITIONED BY RANGE (received_at). Retention 60 дней.
    Под схему AdsetPro (click_id, goal, payout, country, ip, ...).
    ad_id ON DELETE SET NULL — postback'и сохраняются для биллинга/audit
    даже при удалении объявления.
    """

    __tablename__ = "tracker_postback"

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, nullable=False)
    click_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tracker_offer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    goal: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payout: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    fb_ad_id_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ad_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fb_ads.id", ondelete="SET NULL"),
        nullable=True,
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("id", "received_at"),
        Index("ix_tracker_postback_click", "click_id"),
        Index(
            "ix_tracker_postback_ad",
            "ad_id",
            postgresql_where=text("ad_id IS NOT NULL"),
        ),
        Index("ix_tracker_postback_goal", "goal"),
        Index("ix_tracker_postback_received", "received_at"),
        {"postgresql_partition_by": "RANGE (received_at)"},
    )

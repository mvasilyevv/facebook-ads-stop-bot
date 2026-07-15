# -*- coding: utf-8 -*-
"""Live projection of AdSet.pro state keyed by ``source + click_id``."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, Timestamp, UUIDPrimaryKey


class TrackerClickState(UUIDPrimaryKey, Timestamp, Base):
    """Idempotent state for one provider click.

    ``confirmed_deposit`` can only become true after both registration and FTD
    have been received for this exact ``source + click_id``. It is never revoked.
    """

    __tablename__ = "tracker_click_state"

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    click_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ad_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fb_ads.id", ondelete="SET NULL"),
        nullable=True,
    )
    fb_ad_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    attribution_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'unmatched'")
    )
    registration: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    ftd: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    confirmed_deposit: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    registration_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ftd_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_deposit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ftd_revenue: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, server_default=text("0")
    )
    redeposits: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    redeposit_revenue: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, server_default=text("0")
    )
    last_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __table_args__ = (
        UniqueConstraint("source", "click_id", name="uq_tracker_click_state_source_click"),
        Index("ix_tracker_click_state_ad", "ad_id", "last_event_at"),
        Index("ix_tracker_click_state_last_event", "last_event_at"),
        Index(
            "ix_tracker_click_state_unmatched",
            "last_event_at",
            postgresql_where=text("ad_id IS NULL"),
        ),
    )

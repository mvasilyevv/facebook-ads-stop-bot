# -*- coding: utf-8 -*-
"""Append-only лог FSM-событий алертов. PARTITIONED BY month."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base


class AlertEvent(Base):
    """Append-only лог каждой FSM-трансиции (WARNING/STOP/...).

    PARTITIONED BY RANGE (created_at). Retention 365 дней.
    """

    __tablename__ = "alert_events"

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
    stage: Mapped[str] = mapped_column(String(16), nullable=False)  # warning/stop
    state: Mapped[str] = mapped_column(String(16), nullable=False)  # warning_sent/stop_sent/...
    matched_rule_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    open_state_token: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    scan_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        PrimaryKeyConstraint("id", "created_at", name="pk_alert_events"),
        Index("ix_alert_events_ad_created", "ad_id", "created_at"),
        Index("ix_alert_events_stage", "stage"),
        Index("ix_alert_events_state", "state"),
        Index(
            "ix_alert_events_token",
            "open_state_token",
            postgresql_where=text("open_state_token IS NOT NULL"),
        ),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )

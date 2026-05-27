# -*- coding: utf-8 -*-
"""FSM-состояние per объявление (выделено из старой ad_snapshots god-таблицы)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, Timestamp, UUIDPrimaryKey


class AdAlertState(UUIDPrimaryKey, Timestamp, Base):
    """Текущее FSM-состояние per объявление.

    Включает snoozed_until (слитие с alert_snoozes из legacy схемы).
    Retention: 1:1 с fb_ads (CASCADE).
    """

    __tablename__ = "ad_alert_state"

    ad_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fb_ads.id", ondelete="CASCADE"),
        nullable=False,
    )
    alert_state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'normal'"),
    )  # normal/warning_sent/stop_sent/claimed/disabled
    current_stage: Mapped[str | None] = mapped_column(String(16), nullable=True)  # warning/stop
    open_state_token: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    warning_rule_codes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    stop_rule_codes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_scan_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_transition_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("ad_id", name="uq_ad_alert_state_ad"),
        Index("ix_ad_alert_state_state", "alert_state"),
        Index(
            "ix_ad_alert_state_open",
            "ad_id",
            "open_state_token",
            postgresql_where=text("open_state_token IS NOT NULL"),
        ),
        Index(
            "ix_ad_alert_state_snoozed",
            "ad_id",
            postgresql_where=text("snoozed_until IS NOT NULL"),
        ),
        Index("ix_ad_alert_state_last_scan", "last_scan_id"),
    )

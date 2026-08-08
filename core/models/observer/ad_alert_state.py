# -*- coding: utf-8 -*-
"""FSM-состояние per объявление (выделено из старой ad_snapshots god-таблицы)."""

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
    Numeric,
    SmallInteger,
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

    Включает единый persisted snoozed_until.
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
    enable_grace_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    enable_grace_spend_cap: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    enable_grace_baseline_spend: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    enable_grace_cabinet_day_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    enable_grace_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    enable_grace_currency_exponent: Mapped[int | None] = mapped_column(
        SmallInteger,
        nullable=True,
    )
    last_scan_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_transition_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "(enable_grace_until IS NULL "
            "AND enable_grace_spend_cap IS NULL "
            "AND enable_grace_baseline_spend IS NULL "
            "AND enable_grace_cabinet_day_start IS NULL "
            "AND enable_grace_currency IS NULL "
            "AND enable_grace_currency_exponent IS NULL) OR "
            "(enable_grace_until IS NOT NULL "
            "AND enable_grace_spend_cap IS NOT NULL "
            "AND enable_grace_baseline_spend IS NOT NULL "
            "AND enable_grace_cabinet_day_start IS NOT NULL "
            "AND enable_grace_currency IS NOT NULL "
            "AND enable_grace_currency_exponent IS NOT NULL "
            "AND enable_grace_spend_cap > 0 "
            "AND enable_grace_baseline_spend >= 0 "
            "AND enable_grace_baseline_spend < enable_grace_spend_cap "
            "AND enable_grace_until > enable_grace_cabinet_day_start)",
            name="enable_grace_coherent",
        ),
        CheckConstraint(
            "enable_grace_currency IS NULL OR enable_grace_currency ~ '^[A-Z]{3}$'",
            name="enable_grace_currency",
        ),
        CheckConstraint(
            "enable_grace_currency_exponent IS NULL OR enable_grace_currency_exponent IN (0, 2, 3)",
            name="enable_grace_currency_exponent",
        ),
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

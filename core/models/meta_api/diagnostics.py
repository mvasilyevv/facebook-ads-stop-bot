# -*- coding: utf-8 -*-
"""Latest-state Meta diagnostics and bounded reconciliation history."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from core.models.base import Base, Timestamp


class MetaAccountSnapshot(Timestamp, Base):
    """Authoritative Meta timezone/currency for one canonical cabinet ID."""

    __tablename__ = "meta_account_snapshot"
    __table_args__ = (
        CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'",
            name=conv("ck_meta_account_snapshot_currency"),
        ),
        CheckConstraint(
            "(currency IS NULL) = (currency_observed_at IS NULL)",
            name=conv("ck_meta_account_snapshot_currency_observation"),
        ),
    )

    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    timezone_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    currency_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class MetaShadowSpendState(Timestamp, Base):
    """Durable bounded evidence for Meta billing/reporting divergence.

    The incident baseline survives process restarts.  Recovery is
    confirmed only after two later observations show that reported spend has
    caught up with billing movement since this baseline.
    """

    __tablename__ = "meta_shadow_spend_state"
    __table_args__ = (
        CheckConstraint(
            "(incident_baseline_at IS NULL AND incident_baseline_billing_minor IS NULL "
            "AND incident_baseline_reported_minor IS NULL) OR "
            "(incident_baseline_at IS NOT NULL AND incident_baseline_billing_minor IS NOT NULL "
            "AND incident_baseline_reported_minor IS NOT NULL)",
            name="meta_shadow_baseline_complete",
        ),
        CheckConstraint(
            "recovery_candidate_at IS NULL OR incident_baseline_at IS NOT NULL",
            name=conv("ck_meta_shadow_spend_state_candidate_requires_baseline"),
        ),
    )

    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    samples: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    cabinet_day_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    incident_baseline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    incident_baseline_billing_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    incident_baseline_reported_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    recovery_candidate_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

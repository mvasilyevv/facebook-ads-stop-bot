# -*- coding: utf-8 -*-
"""Durable per-cabinet ownership and progress for the observer control plane."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base


class CabinetRuntime(Base):
    """Fenced lease held by exactly one observer actor for a cabinet.

    ``lease_token`` is monotonically incremented whenever ownership changes.  A
    stale actor may keep running in memory, but cannot persist progress or a
    snapshot with an old token.
    """

    __tablename__ = "cabinet_runtime"

    ad_account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_instance: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    lease_token: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_progress_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_snapshot_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index(
            "ix_cabinet_runtime_lease_expiry",
            "lease_expires_at",
            postgresql_where=text("owner_instance IS NOT NULL"),
        ),
        Index("ix_cabinet_runtime_next_scan", "next_scan_at"),
    )

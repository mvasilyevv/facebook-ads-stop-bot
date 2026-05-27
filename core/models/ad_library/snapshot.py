# -*- coding: utf-8 -*-
"""Партиционированный append-only снимок видимости объявления в scan'е."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base


class AdLibrarySnapshot(Base):
    """Append-only snapshot Ad Library.

    Партиционировано RANGE (scanned_at) — по месяцу.
    Composite PK (id, scanned_at) — обязательно для партиций.
    Retention: 14 дней через DROP PARTITION.
    """

    __tablename__ = "ad_library_snapshot"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("gen_random_uuid()"),
    )
    scan_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ad_library_scan.id", ondelete="CASCADE"),
        nullable=False,
    )
    ad_archive_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("ad_library_ad.ad_archive_id", ondelete="CASCADE"),
        nullable=False,
    )
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    position_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("id", "scanned_at", name="pk_ad_library_snapshot"),
        UniqueConstraint(
            "scan_id",
            "ad_archive_id",
            "scanned_at",
            name="uq_ad_library_snapshot_scan_ad",
        ),
        Index(
            "ix_ad_library_snapshot_ad_scanned",
            "ad_archive_id",
            "scanned_at",
        ),
        Index("ix_ad_library_snapshot_scan", "scan_id"),
        {"postgresql_partition_by": "RANGE (scanned_at)"},
    )

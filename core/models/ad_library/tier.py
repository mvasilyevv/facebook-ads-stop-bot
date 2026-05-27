# -*- coding: utf-8 -*-
"""Tier S/A/B/C per scan."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, CreatedAtOnly, UUIDPrimaryKey


class AdLibraryTier(UUIDPrimaryKey, CreatedAtOnly, Base):
    """Tier ранжирование объявления в конкретном scan'е.

    Retention: CASCADE через ad_library_scan (14 дней).
    """

    __tablename__ = "ad_library_tier"

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
    tier: Mapped[str] = mapped_column(String(1), nullable=False)  # S/A/B/C
    score: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    reason_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("scan_id", "ad_archive_id", name="uq_ad_library_tier_scan_ad"),
        Index(
            "ix_ad_library_tier_scan_tier_score",
            "scan_id",
            "tier",
            "score",
        ),
        Index("ix_ad_library_tier_ad", "ad_archive_id"),
    )

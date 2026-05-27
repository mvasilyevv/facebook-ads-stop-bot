# -*- coding: utf-8 -*-
"""Архив топ-винеров Ad Library (S-tier). Hold forever, защищены от cleanup."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, UUIDPrimaryKey


class AdLibraryWinnerArchive(UUIDPrimaryKey, Base):
    """Архив топ-винеров Ad Library.

    FK ad_archive_id: ON DELETE RESTRICT — нельзя удалить ad, если он здесь.
    FK original_scan_id: ON DELETE SET NULL — scan может уйти через 14 дней.
    Retention: forever. Cleanup_worker исключает эту таблицу.
    """

    __tablename__ = "ad_library_winner_archive"

    ad_archive_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("ad_library_ad.ad_archive_id", ondelete="RESTRICT"),
        nullable=False,
    )
    original_scan_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ad_library_scan.id", ondelete="SET NULL"),
        nullable=True,
    )
    slot: Mapped[str] = mapped_column(String(64), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    tier: Mapped[str] = mapped_column(String(1), nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    pinned_by: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # user@tg_id если ручной pin
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("ad_archive_id", name="uq_ad_library_winner_archive_ad"),
        Index(
            "ix_winner_archive_slot_country",
            "slot",
            "country",
            "archived_at",
        ),
        Index(
            "ix_winner_archive_pinned",
            "pinned_by",
            postgresql_where=text("pinned_by IS NOT NULL"),
        ),
    )

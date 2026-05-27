# -*- coding: utf-8 -*-
"""Нормализованная запись объявления Ad Library (BIGINT PK = Meta ad_archive_id)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Date, DateTime, Index, Integer, Numeric, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base


class AdLibraryAd(Base):
    """Объявление Meta Ad Library по уникальному ad_archive_id.

    PK — естественный Meta numeric ID (BIGINT), не UUID.
    Retention: 14 дней без свежих snapshot'ов (если нет в winner_archive).
    """

    __tablename__ = "ad_library_ad"

    ad_archive_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    page_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    page_name: Mapped[str] = mapped_column(String(255), nullable=False)
    page_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    slot: Mapped[str] = mapped_column(String(64), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    started_running_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ad_format: Mapped[str | None] = mapped_column(String(32), nullable=True)  # video/image/carousel
    total_ads_in_group: Mapped[int | None] = mapped_column(Integer, nullable=True)
    classification_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    vertical: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ai_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "ix_ad_library_ad_slot_country",
            "slot",
            "country",
            "started_running_on",
        ),
        Index("ix_ad_library_ad_page", "page_id"),
        Index("ix_ad_library_ad_last_seen", "last_seen_at"),
        Index(
            "ix_ad_library_ad_vertical",
            "vertical",
            postgresql_where=text("vertical IS NOT NULL"),
        ),
    )

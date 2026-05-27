# -*- coding: utf-8 -*-
"""Запуск сканирования Ad Library (slot × country × timestamp)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, UUIDPrimaryKey


class AdLibraryScan(UUIDPrimaryKey, Base):
    """Один scan Ad Library (slot+country).

    Retention: 14 дней (cleanup_worker). CASCADE удаляет snapshot/tier/report/media.
    """

    __tablename__ = "ad_library_scan"

    slot: Mapped[str] = mapped_column(String(64), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    search_type: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # keyword_unordered/keyword_exact_phrase/page
    max_pages: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("10"))
    ads_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # running/done/failed
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    triggered_by: Mapped[str] = mapped_column(String(64), nullable=False)  # user@tg_id/api/cron
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            "ix_ad_library_scan_slot_country",
            "slot",
            "country",
            "started_at",
        ),
        Index("ix_ad_library_scan_status", "status"),
        Index("ix_ad_library_scan_started", "started_at"),
    )

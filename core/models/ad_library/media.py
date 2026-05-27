# -*- coding: utf-8 -*-
"""Медиа Ad Library: видео/картинки + транскрипты + AI summary."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, UUIDPrimaryKey


class AdLibraryMedia(UUIDPrimaryKey, Base):
    """Скачанный медиафайл из Ad Library.

    sha256 UNIQUE — дедуп креативов (один и тот же креатив у разных страниц = одна запись).
    Retention: CASCADE через ad_library_ad. Файлы на диске чистит cleanup_worker.
    """

    __tablename__ = "ad_library_media"

    ad_archive_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("ad_library_ad.ad_archive_id", ondelete="CASCADE"),
        nullable=False,
    )
    media_type: Mapped[str] = mapped_column(String(16), nullable=False)  # video/image/thumbnail
    local_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duration_s: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    downloaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("sha256", name="uq_ad_library_media_sha256"),
        Index("ix_ad_library_media_ad", "ad_archive_id"),
        Index("ix_ad_library_media_type", "media_type"),
    )

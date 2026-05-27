# -*- coding: utf-8 -*-
"""Финальный markdown-отчёт per scan."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, UUIDPrimaryKey


class AdLibraryReport(UUIDPrimaryKey, Base):
    """Отчёт по scan'у: top winners + vertical breakdown + markdown.

    Retention: CASCADE через ad_library_scan.
    """

    __tablename__ = "ad_library_report"

    scan_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ad_library_scan.id", ondelete="CASCADE"),
        nullable=False,
    )
    top_winners_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    vertical_breakdown_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    markdown_report: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("scan_id", name="uq_ad_library_report_scan"),
        Index("ix_ad_library_report_generated", "generated_at"),
    )

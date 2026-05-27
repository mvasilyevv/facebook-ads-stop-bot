# -*- coding: utf-8 -*-
"""Append-only лог входящих webhook'ов от Meta. Partitioned by month."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    PrimaryKeyConstraint,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base


class MetaApiWebhookEvent(Base):
    """Входящий webhook от Meta (ad status change, payment event, ...).

    PARTITIONED BY RANGE (received_at). Retention 90 дней.
    Нет FK на fb_ads — webhook может прийти про объект которого ещё нет в БД.
    """

    __tablename__ = "meta_api_webhook_event"

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    ad_account_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fb_object_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    signature_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("id", "received_at"),
        Index("ix_meta_webhook_event_type", "event_type"),
        Index(
            "ix_meta_webhook_unprocessed",
            "received_at",
            postgresql_where=text("processed_at IS NULL"),
        ),
        Index(
            "ix_meta_webhook_account",
            "ad_account_id",
            postgresql_where=text("ad_account_id IS NOT NULL"),
        ),
        {"postgresql_partition_by": "RANGE (received_at)"},
    )

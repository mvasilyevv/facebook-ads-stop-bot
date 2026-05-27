# -*- coding: utf-8 -*-
"""Event log рекомендаций на включение (не очередь, live-batch-овый)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, CreatedAtOnly, UUIDPrimaryKey


class EnableRecommendation(UUIDPrimaryKey, CreatedAtOnly, Base):
    """Event log рекомендаций enable_recommendation_worker.

    promoted_to_task_id → task_queue.id ON DELETE SET NULL.
    Retention: 30 дней (cleanup_worker).
    """

    __tablename__ = "enable_recommendations"

    ad_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fb_ads.id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    recommendation_level: Mapped[str] = mapped_column(String(16), nullable=False)  # ok/warning
    live_batch_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    promoted_to_task_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("task_queue.id", ondelete="SET NULL"),
        nullable=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_enable_recommendations_idempotency"),
        Index("ix_enable_recs_ad", "ad_id"),
        Index("ix_enable_recs_level", "recommendation_level"),
        Index("ix_enable_recs_batch", "live_batch_started_at"),
        Index(
            "ix_enable_recs_promoted",
            "promoted_to_task_id",
            postgresql_where=text("promoted_to_task_id IS NOT NULL"),
        ),
        Index("ix_enable_recs_created", "created_at"),
    )

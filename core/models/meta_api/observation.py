# -*- coding: utf-8 -*-
"""Snapshot статуса/метрик объявления из Meta Marketing API.

1:1 с fb_ads через UNIQUE(ad_id). Не партиционируется — растёт не быстрее каталога.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, Timestamp, UUIDPrimaryKey


class MetaApiObservation(UUIDPrimaryKey, Timestamp, Base):
    """Последний known-статус объявления из Marketing API.

    Обновляется meta_api_worker при опросе API.
    Изолирован от Vision-пути (не пишет в наблюдаемость observer'а).
    """

    __tablename__ = "meta_api_observation"

    ad_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fb_ads.id", ondelete="CASCADE"),
        nullable=False,
    )
    last_api_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    meta_ad_status: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    api_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    account_id: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("ad_id", name="uq_meta_api_observation_ad"),
        Index("ix_meta_observation_status", "meta_ad_status"),
        Index(
            "ix_meta_observation_account",
            "account_id",
            "last_api_observed_at",
        ),
    )

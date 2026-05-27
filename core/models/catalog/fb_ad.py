# -*- coding: utf-8 -*-
"""Facebook объявление — корень всех downstream FK (fb_ads)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models.base import Base, Timestamp, UUIDPrimaryKey


class FbAd(UUIDPrimaryKey, Timestamp, Base):
    """Объявление Facebook.

    fb_ad_id — Meta numeric ID, UNIQUE (не partial, всегда NOT NULL).
    adset_id — ON DELETE CASCADE: при удалении adset все ads удаляются.
    creative_hash — для дедупа креативов; partial индекс только при NOT NULL.
    Самая горячая таблица — на неё ссылаются alert_state, metrics, alert_events и др.
    """

    __tablename__ = "fb_ads"
    __table_args__ = (
        UniqueConstraint("fb_ad_id", name="uq_fb_ads_fb_ad_id"),
        Index("ix_fb_ads_adset", "adset_id"),
        Index("ix_fb_ads_last_seen", "last_seen_at"),
        Index(
            "ix_fb_ads_active",
            "id",
            postgresql_where=text("is_active = true"),
        ),
        Index(
            "ix_fb_ads_creative_hash",
            "creative_hash",
            postgresql_where=text("creative_hash IS NOT NULL"),
        ),
    )

    adset_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fb_adsets.id", ondelete="CASCADE"),
        nullable=False,
    )
    fb_ad_id: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    ad_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    creative_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    adset: Mapped["FbAdset"] = relationship(  # noqa: F821
        "FbAdset",
        back_populates="ads",
    )

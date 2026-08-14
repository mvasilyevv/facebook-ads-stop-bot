# -*- coding: utf-8 -*-
"""Facebook объявление — корень всех downstream FK (fb_ads)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models.base import Base, Timestamp, UUIDPrimaryKey


class FbAd(UUIDPrimaryKey, Timestamp, Base):
    """Объявление Facebook.

    fb_ad_id — Meta numeric ID, UNIQUE (не partial, всегда NOT NULL).
    adset_id — ON DELETE CASCADE: при удалении adset все ads удаляются.
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
    # Волна 1: превью креатива из Graph (creative.thumbnail_url / image_url).
    # Обновляется upsert'ом на каждом скане — URL Meta истекает (~30 дней).
    # thumbnail — для таблицы (любой тип крео); image — крупно в карточке (только image-крео).
    creative_thumb_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    creative_image_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    # Текущий статус доставки (Active/Paused/In Review/Disapproved/...) — снимается
    # на каждом скане из DOM или маппинга Meta effective_status, обновляется upsert'ом.
    delivery_status: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    # Durable evaluator projection for operator surfaces. Values are written
    # from RuleEvaluation.nearest_stop and never reconstructed in the API.
    nearest_rule_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    nearest_rule_value: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    nearest_rule_threshold: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    nearest_rule_stage: Mapped[str | None] = mapped_column(String(16), nullable=True)
    matched_offer_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
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

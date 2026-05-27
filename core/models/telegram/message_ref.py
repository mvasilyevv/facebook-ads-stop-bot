# -*- coding: utf-8 -*-
"""Ссылки на TG-сообщения для редактирования (единое место delivery state)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, UUIDPrimaryKey


class TelegramMessageRef(UUIDPrimaryKey, Base):
    """Ссылка на конкретное TG-сообщение для редактирования.

    Retention: CASCADE при удалении fb_ads.
    """

    __tablename__ = "telegram_message_refs"

    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ad_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fb_ads.id", ondelete="CASCADE"),
        nullable=False,
    )
    incident_key: Mapped[str] = mapped_column(String(64), nullable=False)
    stream_kind: Mapped[str] = mapped_column(String(16), nullable=False)  # warning/stop/enable/ops
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "chat_id",
            "ad_id",
            "incident_key",
            "stream_kind",
            name="uq_telegram_message_refs_incident",
        ),
        Index("ix_message_refs_ad", "ad_id"),
        Index(
            "ix_message_refs_active",
            "chat_id",
            "ad_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

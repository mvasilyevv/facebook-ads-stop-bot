# -*- coding: utf-8 -*-
"""TG-пользователи, подключённые к боту."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, CreatedAtOnly, UUIDPrimaryKey


class TelegramRecipient(UUIDPrimaryKey, CreatedAtOnly, Base):
    """TG-получатель алертов.

    Retention: revoked_at < NOW() - 365 days → DELETE.
    """

    __tablename__ = "telegram_recipients"

    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # owner/recipient
    invite_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("telegram_invites.id", ondelete="SET NULL"),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "chat_id",
            "telegram_user_id",
            name="uq_telegram_recipients_chat_user",
        ),
        Index(
            "ix_recipients_active",
            "chat_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_recipients_role", "role"),
    )

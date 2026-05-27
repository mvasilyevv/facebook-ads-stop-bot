# -*- coding: utf-8 -*-
"""Invite-коды для подключения новых TG-пользователей."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, CreatedAtOnly, UUIDPrimaryKey


class TelegramInvite(UUIDPrimaryKey, CreatedAtOnly, Base):
    """Invite-код для нового TG-пользователя.

    Retention: 30 дней после COALESCE(used_at, revoked_at, expires_at).
    """

    __tablename__ = "telegram_invites"

    code: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("code", name="uq_telegram_invites_code"),
        Index(
            "ix_invites_active",
            "id",
            postgresql_where=text("used_at IS NULL AND revoked_at IS NULL"),
        ),
        Index("ix_invites_expires", "expires_at"),
    )

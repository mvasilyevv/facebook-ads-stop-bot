# -*- coding: utf-8 -*-
"""Singleton-конфигурация Vision anti-detect браузера."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, SingletonMixin, Timestamp, UUIDPrimaryKey


class VisionConfig(UUIDPrimaryKey, SingletonMixin, Timestamp, Base):
    """Единственная строка с параметрами Vision-браузера.

    Токен и cloud-креды зашифрованы Fernet через core.crypto.
    """

    __tablename__ = "vision_config"

    x_token_encrypted: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )
    profile_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    username_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    team_id_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    folder_id_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Отдельно от updated_at: неудачная попытка не должна ложно
    # инвалидировать browser readiness как будто токен сменился.
    token_refresh_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

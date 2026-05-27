# -*- coding: utf-8 -*-
"""Singleton-конфигурация Vision anti-detect браузера."""

from __future__ import annotations

from typing import Any

from sqlalchemy import String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, SingletonMixin, Timestamp, UUIDPrimaryKey


class VisionConfig(UUIDPrimaryKey, SingletonMixin, Timestamp, Base):
    """Единственная строка с параметрами Vision-браузера.

    x_token_encrypted — Fernet-шифрование через core.crypto.
    column_widths_json — кастомные ширины колонок Ads Manager.
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
    column_widths_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

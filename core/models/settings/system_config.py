# -*- coding: utf-8 -*-
"""Key-value JSONB конфиг для глобальных параметров системы."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, Timestamp, UUIDPrimaryKey


class SystemConfig(UUIDPrimaryKey, Timestamp, Base):
    """Строка key → value JSONB.

    Известные ключи: retention_policy, cleanup_runs, feature_flags, meta_api_account.
    GIN-индекс на value позволяет делать запросы внутрь JSONB.
    """

    __tablename__ = "system_config"
    __table_args__ = (
        UniqueConstraint("key", name="uq_system_config_key"),
        Index("ix_system_config_value_gin", "value", postgresql_using="gin"),
    )

    key: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    value: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

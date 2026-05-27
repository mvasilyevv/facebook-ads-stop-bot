# -*- coding: utf-8 -*-
"""Оффер — корневая сущность каталога (DRC_CR2, KE_CR2, ...)."""

from __future__ import annotations

from sqlalchemy import Boolean, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models.base import Base, Timestamp, UUIDPrimaryKey


class Offer(UUIDPrimaryKey, Timestamp, Base):
    """Оффер.

    code — уникальный код (UNIQUE), используется для матчинга с названиями кампаний.
    is_active — soft-delete флаг; удалять офферы рекомендуется только через UI с проверкой FK.
    """

    __tablename__ = "offers"
    __table_args__ = (
        Index(
            "ix_offers_active",
            "id",
            postgresql_where=text("is_active = true"),
        ),
    )

    code: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        unique=True,
    )
    name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    vertical: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )

    rules: Mapped[list["OfferRule"]] = relationship(  # noqa: F821
        "OfferRule",
        back_populates="offer",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    rule_stats: Mapped[list["OfferRuleStat"]] = relationship(  # noqa: F821
        "OfferRuleStat",
        back_populates="offer",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

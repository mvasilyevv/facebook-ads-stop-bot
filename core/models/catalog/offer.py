# -*- coding: utf-8 -*-
"""Оффер — корневая сущность каталога (DRC_CR2, KE_CR2, ...)."""

from __future__ import annotations

from sqlalchemy import Boolean, Index, String, text
from sqlalchemy.dialects.postgresql import ARRAY
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
    # FB Pixel ID оффера (числовой ID пикселя). Используется при создании кампаний
    # как событие оптимизации (Purchase/FTD). Nullable — задаётся в карточке оффера.
    pixel_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
    # Мульти-кабинет (MULTI_CABINET_PLAN.md): рекламные кабинеты, в которых живёт оффер
    # (числовые ID без префикса act_). Scan set observer'а = union по активным офферам.
    # Пустой список — оффер не участвует в скане (warning в TG); валидация min 1 — на API.
    ad_account_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        server_default=text("'{}'"),
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

# -*- coding: utf-8 -*-
"""Конфигурация 6 стоп-правил per оффер (1:1 с offers)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.schema import conv

from core.models.base import Base, Timestamp, UUIDPrimaryKey


class OfferRule(UUIDPrimaryKey, Timestamp, Base):
    """Пороговые значения стоп-правил для оффера.

    UNIQUE(offer_id) — строго 1:1 с offers.
    Monetary/frequency пороги nullable — соответствующее правило неактивно при NULL.
    Чувствительность всегда задана и ограничена диапазоном 1–100.
    """

    __tablename__ = "offer_rules"
    __table_args__ = (
        UniqueConstraint("offer_id", name="uq_offer_rules_offer_id"),
        CheckConstraint(
            "cpa_threshold IS NULL OR (cpa_threshold > 0 AND cpa_threshold < 'Infinity'::numeric)",
            name="cpa_threshold_positive_finite",
        ),
        CheckConstraint(
            "frequency_threshold IS NULL OR "
            "(frequency_threshold > 0 AND frequency_threshold < 'Infinity'::numeric)",
            name="frequency_threshold_positive_finite",
        ),
        CheckConstraint(
            "stop_percent_of_rule BETWEEN 1 AND 100",
            name="stop_percent_range",
        ),
        CheckConstraint(
            "warning_percent_of_stop BETWEEN 1 AND 100",
            name="warning_percent_range",
        ),
        CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'",
            name=conv("ck_offer_rules_currency"),
        ),
        CheckConstraint(
            "cpa_threshold IS NULL OR currency IS NOT NULL",
            name=conv("ck_offer_rules_cpa_currency_required"),
        ),
    )

    offer_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("offers.id", ondelete="CASCADE"),
        nullable=False,
    )
    cpa_threshold: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    frequency_threshold: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )
    # Чувствительность (per-offer): регулирует НЕ сами правила, а при каком % они
    # срабатывают. stop_percent_of_rule — стоп = N% от базового правила (CPC-база
    # 2%×CPA и т.д.); warning_percent_of_stop — ворнинг = M% от стопа. Дефолт 80/80
    # (ровно как было захардкожено в build_rule_context). Диапазон 1–100 (валидация в API).
    stop_percent_of_rule: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        server_default="80",
    )
    warning_percent_of_stop: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        server_default="80",
    )

    offer: Mapped["Offer"] = relationship(  # noqa: F821
        "Offer",
        back_populates="rules",
    )

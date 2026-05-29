# -*- coding: utf-8 -*-
"""Singleton-конфигурация observer_worker: интервалы, флаги сканирования."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, SingletonMixin, Timestamp, UUIDPrimaryKey


class ObserverConfig(UUIDPrimaryKey, SingletonMixin, Timestamp, Base):
    """Единственная строка с параметрами observer.

    Обновляется через API PUT /settings/observer.
    Читается observer_worker каждый цикл.
    """

    __tablename__ = "observer_config"

    interval_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="90",
    )
    jitter_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="15",
    )
    stale_data_threshold_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="600",
    )
    install_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        server_default="0.50",
    )
    agent_commission_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        server_default="30.0",
    )
    is_scanning_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
    )
    auto_enable_recommendations: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
    )
    # Owner-scoping: тег владельца в названии кампании (например, "MV").
    # Если задан — observer обрабатывает ТОЛЬКО кампании с этим тегом (word-boundary),
    # остальные полностью игнорируются (защита от работы с чужими кампаниями в общем
    # кабинете). NULL/пусто — фильтр выключен (обрабатываются все кампании).
    owner_campaign_tag: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

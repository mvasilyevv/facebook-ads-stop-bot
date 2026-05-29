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
    # Owner-scoping: теги владельца в названии кампании. Поддерживает НЕСКОЛЬКО тегов
    # через запятую (например, "MV" или "MV,ABC,XYZ"). Если задан — observer обрабатывает
    # ТОЛЬКО кампании, чьё название/объявление содержит ЛЮБОЙ из тегов (word-boundary),
    # остальные полностью игнорируются (защита от чужих кампаний в общем кабинете).
    # NULL/пусто — фильтр выключен (обрабатываются все кампании).
    owner_campaign_tag: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    # Канал исполнения toggle-действий (disable/enable). FALSE — через DOM-клик
    # (browser-agent toggle_ad, latency-critical путь). TRUE — через Marketing API
    # (meta_api_mutation pause_ad/activate_ad): точно по ad_id, не промахивается по
    # кнопке/скроллу. Detect всегда через DOM (observer scan), меняется только act.
    # Дефолт TRUE — API основной канал (проверен live: 48 операций, 0 промахов).
    # FALSE — спящий DOM-резерв (фолбэк при сбое Graph API на живой Vision-сессии).
    # При TRUE требуется запущенный meta_api_worker.
    act_via_api: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
    )

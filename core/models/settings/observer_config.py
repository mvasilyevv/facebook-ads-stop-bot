# -*- coding: utf-8 -*-
"""Singleton-конфигурация observer_worker: интервалы, флаги сканирования."""

from __future__ import annotations

from sqlalchemy import ARRAY, Boolean, Integer, String, Text, text
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
        server_default="30",
    )
    # Дефолт FALSE: чистая установка НЕ начинает наблюдение за кабинетом без явного
    # включения (тумблер «Сканирование» на Панели). Защита от случайного скана чужого
    # кабинета сразу после деплоя. Существующий singleton миграцией не трогается.
    is_scanning_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
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
    # Allowlist кампаний для am-режима (#3): фильтр am_tabular по campaign.id IN [...].
    # Пусто — без фильтра по кампаниям (owner_campaign_tag всё равно отсекает чужое в пайплайне).
    # Сужает выборку в общем кабинете, чтобы не тянуть чужие ад'ы.
    campaign_ids: Mapped[list[str]] = mapped_column(
        # Baseline type is TEXT[]; keep ORM metadata identical.
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'"),
    )

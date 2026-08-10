# -*- coding: utf-8 -*-
"""Пресет создания кампании — стабильный переиспользуемый конфиг.

Делит поля CampaignConfig на preset (редко меняется) и run (каждый залив).
Здесь — preset-часть: идентичность кабинета, цель/оптимизация, атрибуция,
шаблоны нейминга/трекинга. Дефолты — по SOP проекта (см. дизайн-док раздел 2).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, Timestamp, UUIDPrimaryKey


class CampaignPreset(UUIDPrimaryKey, Timestamp, Base):
    """Стабильный конфиг залива кампании (переиспользуется между запусками).

    SOP-дефолты:
    - objective=OUTCOME_SALES / optimization_goal=OFFSITE_CONVERSIONS / custom_event_type=PURCHASE
      (событие оптимизации пикселя всегда Purchase/FTD, даже на холодном пикселе);
    - special_ad_categories=["NONE"];
    - cta=PLAY_GAME, text_optimizations=OPT_OUT;
    - click_through_days=1 / view_through_days=1.

    name уникален: пресет адресуется по имени в UI/API.
    """

    __tablename__ = "campaign_preset"

    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    # Идентичность кабинета — без дефолтов (зависит от кабинета владельца).
    act_id: Mapped[str] = mapped_column(String(64), nullable=False)
    page_id: Mapped[str] = mapped_column(String(64), nullable=False)
    pixel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Дефолты оффера/байера для run (run может переопределить).
    offer_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    byer_tag: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Цель и оптимизация — money-критичные дефолты (Purchase-оптимизация).
    objective: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'OUTCOME_SALES'")
    )
    optimization_goal: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'OFFSITE_CONVERSIONS'")
    )
    custom_event_type: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'PURCHASE'")
    )

    # Спецкатегории рекламы (JSONB-массив строк, дефолт ["NONE"]).
    special_ad_categories: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("""'["NONE"]'::jsonb""")
    )

    # CTA + оптимизация текста.
    cta: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'PLAY_GAME'"))
    text_optimizations: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'OPT_OUT'")
    )

    # Окна атрибуции (дни).
    click_through_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    view_through_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )

    # Шаблон url_tags (sub2…sub7 по SOP) — строка с плейсхолдерами.
    url_tags_template: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Шаблон нейминга кампании: {byer} | {offer} | {type} | adset.pro | {date}.
    naming_template: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Доп. preset-поля без отдельных колонок (расширяемость без миграции).
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # TG chat_id создателя (опц.) — NULL если пресет создан через HTTP/UI без TG-контекста.
    created_by_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

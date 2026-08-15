# -*- coding: utf-8 -*-
"""Пресет создания кампании — копируемый снимок повторяемых полей визарда.

Пресет не владеет кампанией и не является живой ссылкой. При применении его
значения копируются в редактируемый черновик, а ``campaign_run.config`` хранит
самостоятельный полный снимок запуска.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, Timestamp, UUIDPrimaryKey


class CampaignPreset(UUIDPrimaryKey, Timestamp, Base):
    """Шаблон повторяемых параметров аудитории, бюджета и нейминга."""

    __tablename__ = "campaign_preset"

    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    # Legacy-поля старого контракта. Новые API/UI их не читают: кабинет, оффер и
    # атрибуция подтверждаются для конкретного запуска. Колонки оставлены, чтобы
    # forward-only миграция не уничтожала ранее сохранённые данные.
    act_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    page_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pixel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    offer_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    byer_tag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    objective: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'OUTCOME_SALES'")
    )
    optimization_goal: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'OFFSITE_CONVERSIONS'")
    )
    custom_event_type: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'PURCHASE'")
    )

    special_ad_categories: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("""'["NONE"]'::jsonb""")
    )
    cta: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'PLAY_GAME'"))
    text_optimizations: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'OPT_OUT'")
    )

    click_through_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    view_through_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )

    # Канонические preset-поля. Пустые genders/placements означают автоматический
    # охват Meta; пустой daily_budget допустим только у legacy-записи до её правки.
    countries: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    age_min: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("21"))
    age_max: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("65"))
    genders: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    placements: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    budget_level: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'campaign'")
    )
    daily_budget: Mapped[str | None] = mapped_column(String(32), nullable=True)

    url_tags_template: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    naming_template: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Legacy extension storage; the snapshot API deliberately ignores it.
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # TG chat_id создателя (опц.) — NULL если пресет создан через HTTP/UI без TG-контекста.
    created_by_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

# -*- coding: utf-8 -*-
"""Записанные планы создания кампаний."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, Timestamp, UUIDPrimaryKey


class CreatorPlan(UUIDPrimaryKey, Timestamp, Base):
    """Записанный план создания кампании (массив PlanAction).

    PlanRun теперь живёт в task_queue (тип plan_run), step_log в payload.
    Retention: не удаляется автоматически, архивирование ручное.
    """

    __tablename__ = "creator_plans"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    variables: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    __table_args__ = (
        Index(
            "uq_creator_plans_name_active",
            "name",
            unique=True,
            postgresql_where=text("is_archived = false"),
        ),
        Index(
            "ix_plans_active",
            "id",
            postgresql_where=text("is_archived = false"),
        ),
    )

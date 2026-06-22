# -*- coding: utf-8 -*-
"""Запуск создания кампании — снимок конфига + прогресс исполнения воркером.

Money-критично: один run = один залив в Meta. idempotency_key (offer+date+хеш
структуры) защищает от двойного создания при retry/гонке. config — полный снимок
CampaignConfig на момент запуска (не зависит от последующих правок пресета).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, Timestamp, UUIDPrimaryKey

# Канон статусов жизненного цикла run (зеркало воркера campaign_creator).
CAMPAIGN_RUN_STATUSES: tuple[str, ...] = (
    "queued",
    "uniquifying",
    "uploading",
    "creating",
    "succeeded",
    "failed",
    "cancelled",
)

_STATUS_IN = ", ".join(f"'{s}'" for s in CAMPAIGN_RUN_STATUSES)


class CampaignRun(UUIDPrimaryKey, Timestamp, Base):
    """Запуск создания кампании.

    Жизненный цикл status:
        queued → uniquifying → uploading → creating → succeeded | failed | cancelled

    progress — инкрементально дописываемый воркером jsonb (этап, счётчики).
    created_meta_ids — id созданных Meta-объектов (campaign/adset/ad) для ревью и cleanup.
    error — текст ошибки при failed (включая partial-create с created_ids в progress).
    """

    __tablename__ = "campaign_run"

    preset_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("campaign_preset.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Полный снимок CampaignConfig на момент запуска (контракт API↔воркер).
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'queued'"))

    progress: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_meta_ids: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Идемпотентность залива — nullable (ad-hoc run без ключа), но UNIQUE когда задан.
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # TG chat_id инициатора (опц.) — NULL если запуск через HTTP/UI.
    created_by_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_campaign_run_idempotency_key"),
        CheckConstraint(
            f"status IN ({_STATUS_IN})",
            name="status",
        ),
        Index("ix_campaign_run_status", "status"),
        Index("ix_campaign_run_created_at", "created_at"),
        Index("ix_campaign_run_preset", "preset_id"),
    )

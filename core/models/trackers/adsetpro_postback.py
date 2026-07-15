# -*- coding: utf-8 -*-
"""Durable inbox положительных postback-событий AdSet.pro.

Семантически близка к tracker_postback (raw postback'и), но отдельная по двум
причинам:
- Явное соответствие плану Этапа 6 (имя таблицы и набор полей).
- event_type/revenue в канонической семантике registration/ftd/redeposit удобнее
  держать как первоклассные колонки, а не вытаскивать из tracker_postback.raw_payload.

Partitioned by RANGE (received_at) — retention 60 дней через cleanup_worker
(после регистрации таблицы в retention_policy).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base

# Transitional compatibility boundary for application-only N-1 rollback.
# Release 0034 canonicalizes all newly accepted positive events, but the
# immediately previous application writes provider values verbatim.  Keep its
# known aliases/statuses insertable until a later migration can prove the old
# release is no longer a rollback target and normalize any rows it produced.
ADSETPRO_TRANSITION_EVENT_TYPES = (
    "registration",
    "ftd",
    "redeposit",
    "reg",
    "signup",
    "hold",
    "cpa_hold",
    "first_deposit",
    "first-deposit",
    "first deposit",
    "accept",
    "cpa_accept",
    "redep",
    "cpa_redep",
    "confirmed_deposit",
    "decline",
    "declined",
    "rejected",
    "trash",
    "baddep",
)
_ADSETPRO_TRANSITION_EVENT_TYPES_SQL = ", ".join(
    repr(value) for value in ADSETPRO_TRANSITION_EVENT_TYPES
)


class AdsetProPostbackEvent(Base):
    """Один принятый положительный postback AdSet.pro.

    Поля:
        id              BigSerial (часть PK вместе с received_at — partitioned).
        received_at     UTC время приёма postback'а endpoint'ом (partition key).
        click_id        ID клика в AdSet.pro — основной ключ дедупа.
        fb_ad_id        Сырой Meta ad id из sub8/ext_sub8; ext_sub6 им не является.
        fb_ad_fk        UUID нашего fb_ads.id, если получилось разрезолвить.
                        ON DELETE SET NULL — postback переживает удаление ад'а.
        event_type      registration / ftd / redeposit.
        revenue         В долларах с центами (Numeric(12,4) — на случай микро-amount).
        currency        ISO 4217 (по умолчанию USD).
        raw_json        Полный JSON-payload для аудита и будущей реклассификации.
        signature_valid Прошёл ли check секрета на endpoint'е (True для боевых;
                        False — для отладочных постбэков без подписи).
        is_duplicate    Результат дедупликации входной попытки. Duplicate audit-row
                        сохраняется без processing task и не участвует в проекции.
        processed_at    Когда событие учтено в RuleContext / агрегатах. NULL пока ingest
                        записал только raw-факт.

    Дедуп: UNIQUE (click_id, event_type, received_at) — обязан включать партиционный
    ключ. Полноценная защита от двойного приёма postback'а одного и того же события.
    """

    __tablename__ = "adsetpro_postback_events"

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'adsetpro'")
    )
    provider_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    click_id: Mapped[str] = mapped_column(String(128), nullable=False)
    fb_ad_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fb_ad_fk: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fb_ads.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    revenue: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    currency: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        server_default=text("'USD'"),
    )
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    signature_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_duplicate: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    attribution_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'unmatched'"),
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("id", "received_at"),
        UniqueConstraint(
            "click_id",
            "event_type",
            "received_at",
            name="uq_adsetpro_postback_dedup",
        ),
        CheckConstraint(
            f"lower(trim(event_type)) IN ({_ADSETPRO_TRANSITION_EVENT_TYPES_SQL})",
            name="adsetpro_event_type",
        ),
        Index("ix_adsetpro_postback_received", "received_at"),
        Index(
            "ix_adsetpro_postback_fb_ad",
            "fb_ad_fk",
            "received_at",
            postgresql_where=text("fb_ad_fk IS NOT NULL"),
        ),
        # Hot-path (H-11/BA-11): load_external_deposits_batch фильтрует по СЫРОМУ
        # fb_ad_id (VARCHAR) + received_at на КАЖДОМ скане. Без этого индекса —
        # seq-scan партиции. Partial (fb_ad_id IS NOT NULL): запрос `fb_ad_id = ANY`
        # не матчит NULL, индекс компактнее.
        Index(
            "ix_adsetpro_postback_fb_ad_id",
            "fb_ad_id",
            "received_at",
            postgresql_where=text("fb_ad_id IS NOT NULL"),
        ),
        Index("ix_adsetpro_postback_click", "click_id", "event_type"),
        Index("ix_adsetpro_postback_source_click", "source", "click_id", "event_type"),
        Index(
            "ix_adsetpro_postback_source_provider",
            "source",
            "provider_event_id",
            postgresql_where=text("provider_event_id IS NOT NULL"),
        ),
        Index(
            "ix_adsetpro_postback_processing",
            "attribution_status",
            "next_retry_at",
            postgresql_where=text("processed_at IS NULL"),
        ),
        {"postgresql_partition_by": "RANGE (received_at)"},
    )

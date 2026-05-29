# -*- coding: utf-8 -*-
"""Базовые классы и mixins для всех ORM-моделей новой схемы.

Принципы:
- SQLAlchemy 2.x с Mapped/mapped_column.
- UUID PK через server-side gen_random_uuid() (Postgres) — единое место генерации.
- Timestamp полные: created_at + updated_at, server-side управление через triggers.
- JSONB всегда (не JSON) для возможности GIN-индексирования.
- TZ-aware timestamps (TIMESTAMPTZ).

Все доменные модули импортируют только из этого файла:
    from core.models.base import Base, UUIDPrimaryKey, Timestamp
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, MetaData, String, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Единая схема naming convention для constraints — нужна для Alembic autogenerate.
# Без неё ALTER TABLE миграции на partition'ах и cross-table FK будут конфликтовать.
_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Базовый класс для всех моделей.

    Использует MetaData с naming convention для consistency Alembic-миграций.
    """

    metadata = MetaData(naming_convention=_NAMING_CONVENTION)

    # JSONB как default для всех Dict-полей — выставляется в каждой модели вручную,
    # но declared_attr-обёртка избавляет от повторения.
    type_annotation_map: dict[Any, Any] = {}


class UUIDPrimaryKey:
    """Mixin: PK = UUID через Postgres gen_random_uuid().

    Использование:
        class MyModel(UUIDPrimaryKey, Timestamp, Base):
            __tablename__ = "my_table"
    """

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


class BigIntPrimaryKey:
    """Mixin: PK = BigSerial (для horizontal-growing таблиц как task_queue, audit_log).

    Использование там, где UUID overhead не оправдан (high-volume append-only).
    """

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )


class Timestamp:
    """Mixin: created_at + updated_at, TZ-aware, server-side defaults.

    updated_at обновляется через onupdate=func.now() — работает только при UPDATE
    через ORM. Если хочется надёжнее — добавить trigger в Alembic-миграции:
        CREATE TRIGGER ... BEFORE UPDATE ON <table>
        FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();
    Решение: пока полагаемся на onupdate (всё пишем через ORM).
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class CreatedAtOnly:
    """Mixin: только created_at (для append-only таблиц без UPDATE).

    Применяется к alert_events, scan_runs, meta_api_audit_log, tracker_postback,
    meta_api_webhook_event — там UPDATE не делается, updated_at — лишний column.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class SingletonMixin:
    """Mixin: singleton-таблицы (observer_config, vision_config, telegram_config).

    Добавляет колонку singleton_key с UNIQUE constraint = 'default'.
    Гарантирует ровно одну строку в таблице.

    Использование:
        class ObserverConfig(UUIDPrimaryKey, SingletonMixin, Timestamp, Base):
            __tablename__ = "observer_config"
            # singleton_key уже определён в mixin
    """

    singleton_key: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        unique=True,
        server_default=text("'default'"),
    )


__all__ = [
    "Base",
    "UUIDPrimaryKey",
    "BigIntPrimaryKey",
    "Timestamp",
    "CreatedAtOnly",
    "SingletonMixin",
]

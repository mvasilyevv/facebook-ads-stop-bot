# -*- coding: utf-8 -*-
"""Singleton-конфигурация Telegram-бота."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, SingletonMixin, Timestamp, UUIDPrimaryKey


class TelegramConfig(UUIDPrimaryKey, SingletonMixin, Timestamp, Base):
    """Единственная строка с токеном бота и ID форум-тредов.

    bot_token_encrypted — Fernet-шифрование через core.crypto.
    poller_heartbeat_at — обновляется telegram_poller каждые ~30s.
    """

    __tablename__ = "telegram_config"

    bot_token_encrypted: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )
    chat_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    forum_warning_thread_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    forum_stop_thread_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    forum_enable_thread_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    forum_ops_thread_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    forum_digest_thread_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    poller_offset: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("0"),
    )
    poller_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

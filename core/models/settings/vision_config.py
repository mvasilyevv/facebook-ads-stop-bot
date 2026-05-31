# -*- coding: utf-8 -*-
"""Singleton-конфигурация Vision anti-detect браузера."""

from __future__ import annotations

from sqlalchemy import Boolean, String, text
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, SingletonMixin, Timestamp, UUIDPrimaryKey


class VisionConfig(UUIDPrimaryKey, SingletonMixin, Timestamp, Base):
    """Единственная строка с параметрами Vision-браузера.

    x_token_encrypted — Fernet-шифрование через core.crypto.
    auto_restart_on_missing_cdp — self-heal Vision-сессии при пропаже primary-вкладки/CDP.
    """

    __tablename__ = "vision_config"

    x_token_encrypted: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )
    profile_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    # Дефолт TRUE: проверенное самовосстановление — поведение по умолчанию, флаг лишь
    # даёт ручной kill-switch для observer-side эскалации reconnect/StartBrowser.
    auto_restart_on_missing_cdp: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )

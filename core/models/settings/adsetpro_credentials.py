# -*- coding: utf-8 -*-
"""Singleton с зашифрованными credentials AdSet.pro (Волна 3 META_INTEGRATION_PLAN §5).

В .env хранится только postback secret и MCP-ключ. Эта таблица позволяет ротировать
их без правки файлов окружения — Fernet-encrypted поверх ENCRYPTION_KEY.
Источник в проде — secrets из .env по умолчанию; БД-Singleton подключим, когда
понадобится ротация (на старте Этапа 6 endpoint всё ещё читает settings.adsetpro_*).
"""

from __future__ import annotations

from sqlalchemy import LargeBinary
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, SingletonMixin, Timestamp, UUIDPrimaryKey


class AdsetProCredentials(UUIDPrimaryKey, SingletonMixin, Timestamp, Base):
    """Зашифрованные API-credentials AdSet.pro.

    Поля:
        api_key_encrypted        — Fernet-blob с MCP-ключом (Bearer для /mcp).
        postback_secret_encrypted — опционально: секрет для X-Postback-Secret header'а
                                    (вторая дорожка, чтобы можно было сменить без
                                    рестарта API).
    """

    __tablename__ = "adsetpro_credentials"

    api_key_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    postback_secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

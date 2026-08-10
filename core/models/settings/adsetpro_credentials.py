# -*- coding: utf-8 -*-
"""Singleton с зашифрованными credentials AdSet.pro.

This table is the production credential source. Bootstrap/import may read
environment values, while runtime access uses the encrypted singleton.
"""

from __future__ import annotations

from sqlalchemy import LargeBinary
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, SingletonMixin, Timestamp, UUIDPrimaryKey


class AdsetProCredentials(UUIDPrimaryKey, SingletonMixin, Timestamp, Base):
    """Зашифрованные API-credentials AdSet.pro.

    Поля:
        api_key_encrypted        — Fernet-blob с MCP-ключом (Bearer для /mcp).
        postback_secret_encrypted — optional secret for the GET query token.
    """

    __tablename__ = "adsetpro_credentials"

    # MCP access and GET postback authentication are independent.  A fresh
    # installation may intentionally configure only the postback secret.
    api_key_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    postback_secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

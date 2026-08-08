# -*- coding: utf-8 -*-
"""Ротация ключей AdSet.pro через таблицу adsetpro_credentials (без рестарта).

Модель как у telegram bot token (БД + Fernet, см. core/crypto.py + core/telegram/service.py),
но колонки — **BYTEA** (не TEXT): храним Fernet-токен как байты ASCII.

Runtime reads use only table adsetpro_credentials (singleton 'default'). Environment
values are bootstrap/import inputs and are never an authentication fallback.

Singleton adsetpro_credentials создаётся frozen baseline — DDL здесь не нужен.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.config import Settings, get_settings, reveal_secret
from core.crypto import decrypt, encrypt

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class AdsetProCredentials:
    """Расшифрованный снимок adsetpro_credentials (singleton 'default')."""

    api_key: str
    postback_secret: str | None


def _decrypt_bytea(raw: object) -> str:
    """BYTEA (bytes/memoryview) → расшифрованная строка. Пусто/ошибка → ""."""
    if not raw:
        return ""
    try:
        token = bytes(raw).decode("utf-8")  # type: ignore[arg-type]
    except (UnicodeDecodeError, TypeError):
        logger.error("adsetpro_credentials: BYTEA не декодируется в ASCII-токен")
        return ""
    return decrypt(token)


async def load_adsetpro_credentials(engine: AsyncEngine) -> AdsetProCredentials | None:
    """Прочитать singleton + расшифровать независимые credentials.

    ``None`` означает, что singleton отсутствует либо оба зашифрованных поля
    пусты/повреждены.  Postback-secret остаётся рабочим без MCP API key: это два
    независимых канала, и внешний GET postback не должен зависеть от доступа к
    MCP.
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT api_key_encrypted, postback_secret_encrypted
                    FROM adsetpro_credentials
                    WHERE singleton_key = 'default'
                    """
                )
            )
        ).first()

    if not row:
        return None

    api_key = _decrypt_bytea(row[0])
    secret = _decrypt_bytea(row[1]) or None
    if not api_key and not secret:
        return None

    return AdsetProCredentials(api_key=api_key, postback_secret=secret)


async def bootstrap_adsetpro_credentials_from_env(
    engine: AsyncEngine,
    *,
    settings: Settings | None = None,
) -> bool:
    """Однократно импортировать env credentials в отсутствующий DB singleton.

    Это release/bootstrap-команда, а не runtime fallback.  Существующая строка
    (включая строку с ``NULL`` после явного отключения) всегда авторитетна.
    ``ON CONFLICT DO NOTHING`` делает повторный/параллельный запуск безопасным.

    Returns:
        ``True`` только если текущий вызов создал singleton.
    """

    async with engine.connect() as conn:
        exists = await conn.scalar(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM adsetpro_credentials
                    WHERE singleton_key = 'default'
                )
                """
            )
        )
    if exists:
        return False

    resolved_settings = settings or get_settings()
    api_key = reveal_secret(resolved_settings.adsetpro_mcp_key).strip()
    postback_secret = reveal_secret(resolved_settings.adsetpro_postback_secret).strip()
    if not api_key and not postback_secret:
        return False

    try:
        api_key_encrypted = encrypt(api_key).encode("utf-8") if api_key else None
        postback_secret_encrypted = (
            encrypt(postback_secret).encode("utf-8") if postback_secret else None
        )
    except Exception as exc:
        # Exception messages from crypto implementations may echo plaintext.
        logger.error(
            "Не удалось зашифровать bootstrap credentials AdSet.pro (error_type=%s)",
            type(exc).__name__,
        )
        raise RuntimeError(
            f"AdSet.pro credential bootstrap encryption failed (error_type={type(exc).__name__})"
        ) from None

    async with engine.begin() as conn:
        inserted = (
            await conn.execute(
                text(
                    """
                    INSERT INTO adsetpro_credentials
                        (singleton_key, api_key_encrypted, postback_secret_encrypted)
                    VALUES ('default', :api_key_encrypted, :postback_secret_encrypted)
                    ON CONFLICT (singleton_key) DO NOTHING
                    RETURNING singleton_key
                    """
                ),
                {
                    "api_key_encrypted": api_key_encrypted,
                    "postback_secret_encrypted": postback_secret_encrypted,
                },
            )
        ).first()

    if inserted is not None:
        logger.info(
            "adsetpro_credentials создан из bootstrap environment "
            "(api_key_configured=%s, postback_secret_configured=%s)",
            bool(api_key),
            bool(postback_secret),
        )
    return inserted is not None


async def resolve_adsetpro_api_key(engine: AsyncEngine) -> str:
    """Return the runtime MCP key from the database, or an empty string when absent."""
    creds = await load_adsetpro_credentials(engine)
    if creds and creds.api_key:
        return creds.api_key
    return ""


async def resolve_adsetpro_postback_secret(engine: AsyncEngine) -> str:
    """Return the runtime postback secret from the database, or empty when absent."""
    creds = await load_adsetpro_credentials(engine)
    if creds and creds.postback_secret:
        return creds.postback_secret
    return ""


async def upsert_adsetpro_credentials(
    engine: AsyncEngine,
    *,
    api_key: str,
    postback_secret: str | None = None,
) -> None:
    """Записать/ротировать ключи в БД (Fernet → BYTEA). Применяется БЕЗ рестарта.

    Этот rotation API требует непустой MCP key. Bootstrap отдельно поддерживает
    postback-only конфигурацию. postback_secret опционален.
    """
    if not api_key:
        raise ValueError("api_key не может быть пустым для rotation upsert")

    api_key_enc = encrypt(api_key).encode("utf-8")
    secret_enc = encrypt(postback_secret).encode("utf-8") if postback_secret else None

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO adsetpro_credentials
                    (singleton_key, api_key_encrypted, postback_secret_encrypted)
                VALUES ('default', :api_key_enc, :secret_enc)
                ON CONFLICT (singleton_key) DO UPDATE SET
                    api_key_encrypted = EXCLUDED.api_key_encrypted,
                    postback_secret_encrypted = EXCLUDED.postback_secret_encrypted,
                    updated_at = NOW()
                """
            ),
            {"api_key_enc": api_key_enc, "secret_enc": secret_enc},
        )
    logger.info(
        "adsetpro_credentials обновлены (api_key%s)", " + postback_secret" if secret_enc else ""
    )


async def create_adsetpro_client(engine: AsyncEngine, **overrides: object):
    """Create an AdsetProClient with the database credential.

    Импорт клиента отложенный — избегаем цикла credentials ↔ client при импорте пакета.
    """
    from core.adset_pro.client import AdsetProClient
    from core.config import get_settings

    settings = get_settings()
    api_key = await resolve_adsetpro_api_key(engine)
    params: dict[str, object] = {
        "api_key": api_key,
        "base_url": settings.adsetpro_base_url,
        "timeout_seconds": settings.adsetpro_timeout_seconds,
    }
    params.update(overrides)
    return AdsetProClient(**params)  # type: ignore[arg-type]

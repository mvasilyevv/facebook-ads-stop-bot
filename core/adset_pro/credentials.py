# -*- coding: utf-8 -*-
"""Ротация ключей AdSet.pro через таблицу adsetpro_credentials (без рестарта).

См. META_INTEGRATION_PLAN.md §5 Волна 4 / Этап 6.

Модель как у telegram bot token (БД + Fernet, см. core/crypto.py + core/telegram/service.py),
но колонки — **BYTEA** (не TEXT): храним Fernet-токен как байты ASCII.

Приоритет чтения: таблица adsetpro_credentials (singleton 'default') → фолбэк на .env
(settings.adsetpro_mcp_key / adsetpro_postback_secret). Так ключ можно ротировать в БД,
не перезапуская воркеры/API; пустая БД-строка → старое поведение из .env.

Singleton adsetpro_credentials создан миграцией 0001 (Волна 3) — DDL здесь не нужен.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

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
    """Прочитать singleton + расшифровать. None если строки нет или api_key пуст/битый."""
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
    if not api_key:
        # Битый/пустой ключ в БД — пусть caller уйдёт в .env-фолбэк, а не получит "".
        return None

    secret = _decrypt_bytea(row[1]) or None
    return AdsetProCredentials(api_key=api_key, postback_secret=secret)


async def resolve_adsetpro_api_key(engine: AsyncEngine, *, fallback: str | None = None) -> str:
    """MCP-ключ: БД → .env-фолбэк. Пустая БД-строка → fallback (или settings.adsetpro_mcp_key)."""
    creds = await load_adsetpro_credentials(engine)
    if creds and creds.api_key:
        return creds.api_key
    if fallback is not None:
        return fallback
    from core.config import get_settings

    return get_settings().adsetpro_mcp_key.get_secret_value()


async def resolve_adsetpro_postback_secret(
    engine: AsyncEngine, *, fallback: str | None = None
) -> str:
    """Секрет входящего postback'а: БД → .env-фолбэк (settings.adsetpro_postback_secret).

    Возвращает "" если ни в БД, ни в .env нет — endpoint трактует это как «не настроен» (503).
    """
    creds = await load_adsetpro_credentials(engine)
    if creds and creds.postback_secret:
        return creds.postback_secret
    if fallback is not None:
        return fallback
    from core.config import get_settings

    return get_settings().adsetpro_postback_secret.get_secret_value()


async def upsert_adsetpro_credentials(
    engine: AsyncEngine,
    *,
    api_key: str,
    postback_secret: str | None = None,
) -> None:
    """Записать/ротировать ключи в БД (Fernet → BYTEA). Применяется БЕЗ рестарта.

    api_key обязателен и непустой (колонка NOT NULL). postback_secret опционален.
    """
    if not api_key:
        raise ValueError(
            "api_key не может быть пустым (adsetpro_credentials.api_key_encrypted NOT NULL)"
        )

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
    """Фабрика AdsetProClient с ключом из БД (фолбэк .env). Клиент НЕ запущен (вызови start()).

    Импорт клиента отложенный — избегаем цикла credentials ↔ client при импорте пакета.
    """
    from core.adset_pro.client import AdsetProClient
    from core.config import get_settings

    settings = get_settings()
    api_key = await resolve_adsetpro_api_key(
        engine, fallback=settings.adsetpro_mcp_key.get_secret_value()
    )
    params: dict[str, object] = {
        "api_key": api_key,
        "base_url": settings.adsetpro_base_url,
        "timeout_seconds": settings.adsetpro_timeout_seconds,
    }
    params.update(overrides)
    return AdsetProClient(**params)  # type: ignore[arg-type]

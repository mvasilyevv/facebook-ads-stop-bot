# -*- coding: utf-8 -*-
"""Интеграционный: adsetpro_credentials — ротация ключа БД-first с фолбэком на .env.

Singleton 'default' сохраняется/восстанавливается в фикстуре (БД общая — нельзя
затирать реальные ключи). Проверяет Fernet-roundtrip через BYTEA + приоритет БД→env.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.adset_pro.credentials import (
    load_adsetpro_credentials,
    resolve_adsetpro_api_key,
    resolve_adsetpro_postback_secret,
    upsert_adsetpro_credentials,
)


@pytest_asyncio.fixture
async def preserve_adsetpro_credentials(pg_engine):
    """Снимок singleton до теста + восстановление после (не теряем реальные ключи)."""
    async with pg_engine.connect() as conn:
        snap = (
            await conn.execute(
                text(
                    "SELECT api_key_encrypted, postback_secret_encrypted "
                    "FROM adsetpro_credentials WHERE singleton_key = 'default'"
                )
            )
        ).first()

    async def _clear():
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM adsetpro_credentials WHERE singleton_key = 'default'")
            )

    await _clear()
    yield pg_engine
    await _clear()
    if snap is not None:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO adsetpro_credentials
                        (singleton_key, api_key_encrypted, postback_secret_encrypted)
                    VALUES ('default', :a, :s)
                    """
                ),
                {"a": snap[0], "s": snap[1]},
            )


# Roundtrip: upsert ключ+секрет → load расшифровывает обратно (Fernet поверх BYTEA).
@pytest.mark.asyncio
async def test_upsert_and_load_roundtrip(preserve_adsetpro_credentials) -> None:
    engine = preserve_adsetpro_credentials
    await upsert_adsetpro_credentials(
        engine, api_key="mcp_live_KEY_123", postback_secret="pb_secret_XYZ"
    )
    creds = await load_adsetpro_credentials(engine)
    assert creds is not None
    assert creds.api_key == "mcp_live_KEY_123"
    assert creds.postback_secret == "pb_secret_XYZ"


# Ротация: повторный upsert меняет ключ; БД-значение имеет приоритет над .env.
@pytest.mark.asyncio
async def test_resolve_api_key_prefers_db_over_env(preserve_adsetpro_credentials) -> None:
    engine = preserve_adsetpro_credentials
    await upsert_adsetpro_credentials(engine, api_key="db_key_v1")
    assert await resolve_adsetpro_api_key(engine, fallback="env_key") == "db_key_v1"

    # Ротация без рестарта — следующий resolve берёт новый ключ.
    await upsert_adsetpro_credentials(engine, api_key="db_key_v2")
    assert await resolve_adsetpro_api_key(engine, fallback="env_key") == "db_key_v2"


# Нет строки в БД → resolve уходит на .env-фолбэк.
@pytest.mark.asyncio
async def test_resolve_api_key_falls_back_to_env_when_no_row(
    preserve_adsetpro_credentials,
) -> None:
    engine = preserve_adsetpro_credentials  # фикстура уже удалила строку
    assert await load_adsetpro_credentials(engine) is None
    assert await resolve_adsetpro_api_key(engine, fallback="env_fallback_key") == "env_fallback_key"


# postback secret: БД-first, при отсутствии — .env-фолбэк (пусто → 503-семантика у endpoint'а).
@pytest.mark.asyncio
async def test_resolve_postback_secret_db_then_env(preserve_adsetpro_credentials) -> None:
    engine = preserve_adsetpro_credentials
    # Без секрета в БД (только api_key) → фолбэк на env.
    await upsert_adsetpro_credentials(engine, api_key="k", postback_secret=None)
    assert await resolve_adsetpro_postback_secret(engine, fallback="env_secret") == "env_secret"

    # С секретом в БД → берётся из БД.
    await upsert_adsetpro_credentials(engine, api_key="k", postback_secret="db_secret")
    assert await resolve_adsetpro_postback_secret(engine, fallback="env_secret") == "db_secret"


# Битый BYTEA (не Fernet-токен) → load возвращает None → resolve уходит на фолбэк.
@pytest.mark.asyncio
async def test_corrupt_blob_falls_back(preserve_adsetpro_credentials) -> None:
    engine = preserve_adsetpro_credentials
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO adsetpro_credentials
                    (singleton_key, api_key_encrypted, postback_secret_encrypted)
                VALUES ('default', :a, NULL)
                """
            ),
            {"a": b"\x00\x01not-a-fernet-token\xff"},
        )
    # Декодирование/дешифровка падает → None, а не пустой/мусорный ключ.
    assert await load_adsetpro_credentials(engine) is None
    assert await resolve_adsetpro_api_key(engine, fallback="safe_env") == "safe_env"


# Пустой api_key запрещён (колонка NOT NULL) — upsert бросает ValueError.
@pytest.mark.asyncio
async def test_upsert_empty_api_key_raises(preserve_adsetpro_credentials) -> None:
    engine = preserve_adsetpro_credentials
    with pytest.raises(ValueError):
        await upsert_adsetpro_credentials(engine, api_key="")

# -*- coding: utf-8 -*-
"""Интеграционный: DB-only runtime и one-shot env bootstrap AdSet.pro.

Singleton 'default' сохраняется/восстанавливается в фикстуре (БД общая — нельзя
затирать реальные ключи). Проверяет Fernet-roundtrip и DB authority.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.adset_pro.credentials import (
    bootstrap_adsetpro_credentials_from_env,
    load_adsetpro_credentials,
    resolve_adsetpro_api_key,
    resolve_adsetpro_postback_secret,
    upsert_adsetpro_credentials,
)
from core.crypto import decrypt


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


# Ротация: повторный upsert меняет ключ без рестарта.
@pytest.mark.asyncio
async def test_resolve_api_key_uses_database_value(preserve_adsetpro_credentials) -> None:
    engine = preserve_adsetpro_credentials
    await upsert_adsetpro_credentials(engine, api_key="db_key_v1")
    assert await resolve_adsetpro_api_key(engine) == "db_key_v1"

    # Ротация без рестарта — следующий resolve берёт новый ключ.
    await upsert_adsetpro_credentials(engine, api_key="db_key_v2")
    assert await resolve_adsetpro_api_key(engine) == "db_key_v2"


@pytest.mark.asyncio
async def test_resolve_api_key_is_empty_when_database_row_is_absent(
    preserve_adsetpro_credentials,
) -> None:
    engine = preserve_adsetpro_credentials  # фикстура уже удалила строку
    assert await load_adsetpro_credentials(engine) is None
    assert await resolve_adsetpro_api_key(engine) == ""


@pytest.mark.asyncio
async def test_resolve_postback_secret_uses_database_value(preserve_adsetpro_credentials) -> None:
    engine = preserve_adsetpro_credentials
    await upsert_adsetpro_credentials(engine, api_key="k", postback_secret=None)
    assert await resolve_adsetpro_postback_secret(engine) == ""

    # С секретом в БД → берётся из БД.
    await upsert_adsetpro_credentials(engine, api_key="k", postback_secret="db_secret")
    assert await resolve_adsetpro_postback_secret(engine) == "db_secret"


@pytest.mark.asyncio
async def test_env_bootstrap_is_concurrency_safe_encrypted_and_supports_postback_only(
    preserve_adsetpro_credentials,
) -> None:
    engine = preserve_adsetpro_credentials
    postback_secret = "POSTBACK_ONLY_SECRET"
    settings = SimpleNamespace(
        adsetpro_mcp_key="",
        adsetpro_postback_secret=postback_secret,
    )

    results = await asyncio.gather(
        *(bootstrap_adsetpro_credentials_from_env(engine, settings=settings) for _ in range(4))
    )

    assert results.count(True) == 1
    assert await resolve_adsetpro_api_key(engine) == ""
    assert await resolve_adsetpro_postback_secret(engine) == postback_secret
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT api_key_encrypted, postback_secret_encrypted
                    FROM adsetpro_credentials
                    WHERE singleton_key = 'default'
                    """
                )
            )
        ).all()
    assert len(rows) == 1
    assert rows[0][0] is None
    assert bytes(rows[0][1]).decode() != postback_secret
    assert decrypt(bytes(rows[0][1]).decode()) == postback_secret


@pytest.mark.asyncio
async def test_existing_database_credentials_block_env_reimport(
    preserve_adsetpro_credentials,
) -> None:
    engine = preserve_adsetpro_credentials
    await upsert_adsetpro_credentials(
        engine,
        api_key="db-key",
        postback_secret="db-secret",
    )

    inserted = await bootstrap_adsetpro_credentials_from_env(
        engine,
        settings=SimpleNamespace(
            adsetpro_mcp_key="env-key",
            adsetpro_postback_secret="env-secret",
        ),
    )

    assert inserted is False
    assert await resolve_adsetpro_api_key(engine) == "db-key"
    assert await resolve_adsetpro_postback_secret(engine) == "db-secret"


@pytest.mark.asyncio
async def test_corrupt_blob_leaves_runtime_unconfigured(preserve_adsetpro_credentials) -> None:
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
    assert await resolve_adsetpro_api_key(engine) == ""


# Rotation-upsert требует API key; postback-only создаёт bootstrap-функция.
@pytest.mark.asyncio
async def test_upsert_empty_api_key_raises(preserve_adsetpro_credentials) -> None:
    engine = preserve_adsetpro_credentials
    with pytest.raises(ValueError):
        await upsert_adsetpro_credentials(engine, api_key="")

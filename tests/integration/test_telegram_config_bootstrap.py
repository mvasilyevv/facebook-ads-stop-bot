# -*- coding: utf-8 -*-
"""Integration: explicit Telegram env adoption and DB-only runtime."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import text

from core.config import get_settings
from core.crypto import decrypt, encrypt
from core.telegram.service import (
    bootstrap_telegram_config_from_env,
    load_telegram_config,
)


def _bootstrap_settings(token: str) -> SimpleNamespace:
    return SimpleNamespace(
        telegram_bot_token=SecretStr(token),
        frontend_origin="https://app.example.test",
        telegram_webhook_secret=SecretStr("integration-webhook-secret"),
    )


@pytest_asyncio.fixture
async def clean_telegram_config(pg_engine):
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_config WHERE singleton_key = 'default'"))
    yield
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_config WHERE singleton_key = 'default'"))


@pytest.mark.asyncio
async def test_explicit_bootstrap_is_encrypted_idempotent_and_concurrency_safe(
    pg_engine, clean_telegram_config
) -> None:
    env_token = "123456789:INTEGRATION_BOOTSTRAP_TOKEN"
    settings = _bootstrap_settings(env_token)

    inserted = await asyncio.gather(
        *(bootstrap_telegram_config_from_env(pg_engine, settings=settings) for _ in range(4))
    )

    assert inserted.count(True) == 1
    assert inserted.count(False) == 3
    config = await load_telegram_config(pg_engine)
    assert config is not None
    assert config.bot_token == env_token
    async with pg_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT bot_token_encrypted
                    FROM telegram_config
                    WHERE singleton_key = 'default'
                    """
                )
            )
        ).all()
    assert len(rows) == 1
    assert rows[0][0] != env_token
    assert decrypt(rows[0][0]) == env_token


@pytest.mark.asyncio
async def test_runtime_missing_row_does_not_adopt_environment(
    pg_engine, clean_telegram_config, monkeypatch
) -> None:
    monkeypatch.setattr(
        get_settings(),
        "telegram_bot_token",
        SecretStr("123456789:RUNTIME_MUST_NOT_IMPORT"),
    )

    assert await load_telegram_config(pg_engine) is None

    async with pg_engine.connect() as conn:
        exists = await conn.scalar(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM telegram_config
                    WHERE singleton_key = 'default'
                )
                """
            )
        )
    assert exists is False


@pytest.mark.asyncio
async def test_database_and_explicit_ui_disable_remain_authoritative(
    pg_engine, clean_telegram_config
) -> None:
    ui_encrypted = encrypt("ui-token")
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_config (singleton_key, bot_token_encrypted)
                VALUES ('default', :token)
                """
            ),
            {"token": ui_encrypted},
        )

    assert (
        await bootstrap_telegram_config_from_env(
            pg_engine,
            settings=_bootstrap_settings("env-token"),
        )
        is False
    )
    configured = await load_telegram_config(pg_engine)
    assert configured is not None
    assert configured.bot_token == "ui-token"

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE telegram_config
                SET bot_token_encrypted = '', updated_at = NOW()
                WHERE singleton_key = 'default'
                """
            )
        )

    assert (
        await bootstrap_telegram_config_from_env(
            pg_engine,
            settings=_bootstrap_settings("env-token-after-disable"),
        )
        is False
    )
    assert await load_telegram_config(pg_engine) is None
    async with pg_engine.connect() as conn:
        encrypted = await conn.scalar(
            text(
                """
                SELECT bot_token_encrypted
                FROM telegram_config
                WHERE singleton_key = 'default'
                """
            )
        )
    assert encrypted == ""

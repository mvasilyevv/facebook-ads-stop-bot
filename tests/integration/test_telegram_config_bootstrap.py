# -*- coding: utf-8 -*-
"""Integration: env bootstrap telegram_config в реальном Postgres."""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import text

from core.config import get_settings
from core.crypto import decrypt, encrypt
from core.telegram.service import load_telegram_config


@pytest_asyncio.fixture
async def clean_telegram_config(pg_engine):
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_config WHERE singleton_key = 'default'"))
    yield
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_config WHERE singleton_key = 'default'"))


@pytest.mark.asyncio
async def test_env_bootstrap_is_encrypted_idempotent_and_concurrency_safe(
    pg_engine, clean_telegram_config, monkeypatch
) -> None:
    env_token = "123456789:INTEGRATION_BOOTSTRAP_TOKEN"
    monkeypatch.setattr(get_settings(), "telegram_bot_token", SecretStr(env_token))

    configs = await asyncio.gather(*(load_telegram_config(pg_engine) for _ in range(4)))

    assert all(config is not None and config.bot_token == env_token for config in configs)
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
async def test_database_and_explicit_ui_disable_remain_authoritative(
    pg_engine, clean_telegram_config, monkeypatch
) -> None:
    monkeypatch.setattr(get_settings(), "telegram_bot_token", SecretStr("env-token"))
    ui_encrypted = encrypt("ui-token")
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_config (singleton_key, bot_token_encrypted, poller_offset)
                VALUES ('default', :token, 23)
                """
            ),
            {"token": ui_encrypted},
        )

    configured = await load_telegram_config(pg_engine)
    assert configured is not None
    assert configured.bot_token == "ui-token"
    assert configured.poller_offset == 23

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

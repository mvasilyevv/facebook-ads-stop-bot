# -*- coding: utf-8 -*-
"""Тесты сервисного слоя Telegram: runtime-конфиг и revoke lifecycle."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_async_session(*, scalar_return=None, scalar_side_effect=None):
    """Создаёт мок async-сессии SQLAlchemy."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.scalar = AsyncMock(side_effect=scalar_side_effect, return_value=scalar_return)
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    return session


def _make_session_factory(session):
    """Создаёт фабрику, возвращающую один и тот же мок сессии."""
    return MagicMock(return_value=session)


# Проверяем, что пустая строка telegram_settings не отключает env-fallback.
@pytest.mark.asyncio
async def test_load_runtime_config_uses_env_when_db_row_is_blank():
    """Пустая DB-запись без токена/авторизации не должна блокировать fallback из .env."""
    from core.telegram.service import load_telegram_runtime_config

    blank_row = SimpleNamespace(
        bot_token_encrypted="",
        chat_id="",
        auth_code="",
        bot_username="",
        owner_telegram_user_id="",
        is_authorized=False,
    )
    session = _make_async_session(scalar_return=blank_row)
    factory = _make_session_factory(session)

    with patch("core.telegram.service.get_session_factory", return_value=factory):
        token, destinations = await load_telegram_runtime_config(
            fallback_token="env-token",
            fallback_chat_id="123456",
        )

    assert token == "env-token"
    assert len(destinations) == 1
    assert destinations[0].chat_id == "123456"
    assert destinations[0].is_primary is True


# Проверяем, что DB-конфиг в статусе ожидания авторизации режет env-fallback.
@pytest.mark.asyncio
async def test_load_runtime_config_blocks_env_when_db_waits_for_authorization():
    """Если Telegram уже настраивается через UI, runtime не должен уходить в старый env-чат."""
    from core.telegram.service import load_telegram_runtime_config

    waiting_row = SimpleNamespace(
        bot_token_encrypted="encrypted-token",
        chat_id="",
        auth_code="123456",
        bot_username="my_bot",
        owner_telegram_user_id="",
        is_authorized=False,
    )
    session = _make_async_session(scalar_return=waiting_row)
    factory = _make_session_factory(session)

    with (
        patch("core.telegram.service.get_session_factory", return_value=factory),
        patch("core.telegram.service.decrypt", return_value="db-token"),
    ):
        token, destinations = await load_telegram_runtime_config(
            fallback_token="env-token",
            fallback_chat_id="123456",
        )

    assert token == ""
    assert destinations == []


# Проверяем, что revoke удаляет получателей и отзывает активные инвайты.
@pytest.mark.asyncio
async def test_revoke_telegram_access_records_cleans_recipients_and_invites():
    """revoke helper должен выполнить оба действия: delete recipients и revoke invites."""
    from core.telegram.service import revoke_telegram_access_records

    session = _make_async_session()

    await revoke_telegram_access_records(session)

    assert session.execute.await_count == 2

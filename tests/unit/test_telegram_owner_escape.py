# -*- coding: utf-8 -*-
"""Тесты, что пустой owner_telegram_user_id не даёт никому автоматически роль OWNER."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_async_session(*, scalar_side_effect=None, scalar_return=None):
    """Создаёт мок async-сессии с управляемым scalar."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.scalar = AsyncMock(side_effect=scalar_side_effect, return_value=scalar_return)
    return session


def _make_factory(session):
    """Фабрика, возвращающая один и тот же мок сессии."""
    return MagicMock(return_value=session)


# Если owner_telegram_user_id пустой — никто не должен получить OWNER-роль.
@pytest.mark.asyncio
async def test_empty_owner_telegram_user_id_does_not_grant_owner_role():
    """При пустом owner_telegram_user_id ни один пользователь правильного чата
    не получает OWNER-роль автоматически. Должен пойти стандартный flow с auth_code."""
    from core.telegram import service

    settings_row = SimpleNamespace(
        is_authorized=True,
        chat_id="-1003701505954",
        owner_telegram_user_id="",  # пусто!
        owner_username="",
        owner_first_name="",
    )
    # Первый scalar — настройки, второй — поиск recipient (нет такого).
    session = _make_async_session(scalar_side_effect=[settings_row, None])
    factory = _make_factory(session)

    with patch.object(service, "get_session_factory", return_value=factory):
        access = await service.resolve_telegram_access(
            chat_id="-1003701505954",
            telegram_user_id="999",
            chat_type="supergroup",
        )

    # Никакого OWNER-доступа быть не должно. Recipient тоже нет.
    assert access is None


# Whitespace в owner_telegram_user_id тоже не даёт OWNER-доступ.
@pytest.mark.asyncio
async def test_whitespace_owner_telegram_user_id_does_not_grant_owner_role():
    """owner_telegram_user_id состоит только из пробелов — обращаться как к пустому."""
    from core.telegram import service

    settings_row = SimpleNamespace(
        is_authorized=True,
        chat_id="-1003701505954",
        owner_telegram_user_id="   ",
        owner_username="",
        owner_first_name="",
    )
    session = _make_async_session(scalar_side_effect=[settings_row, None])
    factory = _make_factory(session)

    with patch.object(service, "get_session_factory", return_value=factory):
        access = await service.resolve_telegram_access(
            chat_id="-1003701505954",
            telegram_user_id="42",
            chat_type="supergroup",
        )

    assert access is None


# Placeholder-recipient с пустым telegram_user_id не даёт доступ.
@pytest.mark.asyncio
async def test_placeholder_recipient_with_empty_user_id_does_not_grant_access():
    """Recipient-запись с пустым telegram_user_id (placeholder до первого /start)
    не должна давать роль произвольному пользователю."""
    from core.telegram import service

    settings_row = SimpleNamespace(
        is_authorized=True,
        chat_id="-1003701505954",
        owner_telegram_user_id="111",
        owner_username="real_owner",
        owner_first_name="Real Owner",
    )

    # Recipient с пустым telegram_user_id не должен матчиться.
    # После фикса фильтр строгий: telegram_user_id == telegram_user_id (нашего юзера).
    # Поскольку placeholder имеет "", он не совпадёт с "222", запрос вернёт None.
    session = _make_async_session(scalar_side_effect=[settings_row, None])
    factory = _make_factory(session)

    with patch.object(service, "get_session_factory", return_value=factory):
        access = await service.resolve_telegram_access(
            chat_id="-1003701505954",
            telegram_user_id="222",
            chat_type="supergroup",
        )

    assert access is None


# Существующий настоящий owner с совпадающим telegram_user_id всё ещё получает OWNER.
@pytest.mark.asyncio
async def test_correct_owner_id_still_gets_owner_role():
    """Baseline: при корректном owner_telegram_user_id и совпадении — выдаётся OWNER."""
    from core.domain import TelegramUserRole
    from core.telegram import service

    settings_row = SimpleNamespace(
        is_authorized=True,
        chat_id="-1003701505954",
        owner_telegram_user_id="123",
        owner_username="owner",
        owner_first_name="Mark",
    )
    session = _make_async_session(scalar_side_effect=[settings_row])
    factory = _make_factory(session)

    with patch.object(service, "get_session_factory", return_value=factory):
        access = await service.resolve_telegram_access(
            chat_id="-1003701505954",
            telegram_user_id="123",
            chat_type="supergroup",
        )

    assert access is not None
    assert access.role == TelegramUserRole.OWNER.value


# Пустой telegram_user_id у пользователя (странный edge-case) не даёт доступ.
@pytest.mark.asyncio
async def test_empty_user_telegram_id_does_not_grant_recipient_access():
    """Если у поступающего callback пустой user_id — recipient-доступ не выдаём."""
    from core.telegram import service

    settings_row = SimpleNamespace(
        is_authorized=True,
        chat_id="-1003701505954",
        owner_telegram_user_id="111",
        owner_username="real_owner",
        owner_first_name="",
    )
    session = _make_async_session(scalar_side_effect=[settings_row, None])
    factory = _make_factory(session)

    with patch.object(service, "get_session_factory", return_value=factory):
        access = await service.resolve_telegram_access(
            chat_id="-1003701505954",
            telegram_user_id="",
            chat_type="supergroup",
        )

    assert access is None

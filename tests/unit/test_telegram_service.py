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


# Проверяем, что forum-runtime отдаёт один group destination с topic ids и без user-чатов.
@pytest.mark.asyncio
async def test_load_runtime_config_returns_single_forum_destination():
    """Forum group должен доставлять уведомления в одну supergroup с routing по topics."""
    from core.domain import TelegramDeliveryMode
    from core.telegram.service import load_telegram_runtime_config

    settings_row = SimpleNamespace(
        bot_token_encrypted="encrypted-token",
        chat_id="-1003701505954",
        auth_code="",
        bot_username="adguard_bot",
        owner_telegram_user_id="42",
        owner_username="owner",
        owner_first_name="Иван",
        is_authorized=True,
        delivery_mode=TelegramDeliveryMode.FORUM_GROUP.value,
        control_topic_id=11,
        warning_topic_id=13,
        stop_topic_id=14,
        enable_topic_id=15,
    )
    session = _make_async_session(scalar_return=settings_row)
    session.execute = AsyncMock()
    factory = _make_session_factory(session)

    with (
        patch("core.telegram.service.get_session_factory", return_value=factory),
        patch("core.telegram.service.decrypt", return_value="db-token"),
    ):
        token, destinations = await load_telegram_runtime_config()

    assert token == "db-token"
    assert len(destinations) == 1
    destination = destinations[0]
    assert destination.chat_id == "-1003701505954"
    assert destination.control_topic_id == 11
    assert destination.warning_topic_id == 13
    assert destination.stop_topic_id == 14
    assert destination.enable_topic_id == 15


# Проверяем, что доступ recipient в forum-группе ищется по chat_id и telegram_user_id.
@pytest.mark.asyncio
async def test_resolve_telegram_access_uses_group_user_pair_in_forum_mode():
    """Recipient в общей группе должен авторизоваться по паре group chat + user id."""
    from core.domain import TelegramDeliveryMode
    from core.telegram.service import resolve_telegram_access

    settings_row = SimpleNamespace(
        is_authorized=True,
        chat_id="-1003701505954",
        owner_telegram_user_id="42",
        owner_username="owner",
        owner_first_name="Иван",
        delivery_mode=TelegramDeliveryMode.FORUM_GROUP.value,
        control_topic_id=11,
    )
    recipient_row = SimpleNamespace(
        chat_id="-1003701505954",
        telegram_user_id="77",
        username="guest",
        first_name="Гость",
        role="recipient",
        is_active=True,
    )
    session = _make_async_session(scalar_side_effect=[settings_row, recipient_row])
    factory = _make_session_factory(session)

    with patch("core.telegram.service.get_session_factory", return_value=factory):
        access = await resolve_telegram_access(
            chat_id="-1003701505954",
            telegram_user_id="77",
            chat_type="supergroup",
        )

    assert access is not None
    assert access.role == "recipient"
    assert access.control_topic_id == 11
    assert access.delivery_mode == TelegramDeliveryMode.FORUM_GROUP.value


# Проверяем, что recipient с другим telegram_user_id не получает доступ в forum mode.
@pytest.mark.asyncio
async def test_resolve_telegram_access_rejects_mismatched_forum_user():
    """В forum-группе доступ должен быть привязан к точной паре chat_id + user_id."""
    from core.domain import TelegramDeliveryMode
    from core.telegram.service import resolve_telegram_access

    settings_row = SimpleNamespace(
        is_authorized=True,
        chat_id="-1003701505954",
        owner_telegram_user_id="42",
        owner_username="owner",
        owner_first_name="Иван",
        delivery_mode=TelegramDeliveryMode.FORUM_GROUP.value,
        control_topic_id=11,
    )
    recipient_row = SimpleNamespace(
        chat_id="-1003701505954",
        telegram_user_id="999",
        username="guest",
        first_name="Гость",
        role="recipient",
        is_active=True,
    )
    session = _make_async_session(scalar_side_effect=[settings_row, recipient_row])
    factory = _make_session_factory(session)

    with patch("core.telegram.service.get_session_factory", return_value=factory):
        access = await resolve_telegram_access(
            chat_id="-1003701505954",
            telegram_user_id="77",
            chat_type="supergroup",
        )

    assert access is None


# Проверяем, что статус forum-cutover учитывает только целевую supergroup.
def test_forum_cutover_status_requires_target_group():
    """Forum-cutover не должен считаться готовым для любого произвольного chat_id."""
    from core.domain import TelegramDeliveryMode
    from core.telegram.service import forum_cutover_status_from_settings

    wrong_group_row = SimpleNamespace(
        bot_token_encrypted="encrypted-token",
        chat_id="-1000000000000",
        delivery_mode=TelegramDeliveryMode.FORUM_GROUP.value,
        control_topic_id=11,
        warning_topic_id=13,
        stop_topic_id=14,
        enable_topic_id=15,
        is_authorized=True,
    )

    assert forum_cutover_status_from_settings(wrong_group_row) == "NOT_STARTED"

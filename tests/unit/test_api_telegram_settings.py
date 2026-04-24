# -*- coding: utf-8 -*-
"""Тесты API для Telegram forum-group cutover и настроек."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from core.domain import TelegramDeliveryMode, TelegramUserRole


@pytest.fixture
def mock_db():
    """Мок async DB-сессии для Telegram API."""
    db = AsyncMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    return db


# Проверяем, что Vision settings зарегистрированы и для GET, и для PUT на одном пути.
def test_vision_settings_route_supports_get_and_put():
    from apps.api.main import app

    methods = set()
    for route in app.routes:
        if getattr(route, "path", "") == "/api/settings/vision":
            methods.update(getattr(route, "methods", set()))

    assert "GET" in methods
    assert "PUT" in methods


# Проверяем, что GET настроек Telegram отдаёт forum-group поля и activation command.
@pytest.mark.asyncio
async def test_get_telegram_settings_returns_forum_group_fields(mock_db):
    from apps.api.routers.settings import get_telegram_settings

    invite = SimpleNamespace(
        code="654321",
        role=TelegramUserRole.RECIPIENT.value,
        expires_at=datetime(2026, 3, 30, 12, 0, tzinfo=UTC),
    )
    row = SimpleNamespace(
        singleton_key="default",
        bot_token_encrypted="enc",
        chat_id="-1003701505954",
        is_authorized=False,
        bot_username="adguard_fb_bot",
        auth_code="123456",
        delivery_mode=TelegramDeliveryMode.FORUM_GROUP.value,
        control_topic_id=11,
        warning_topic_id=13,
        stop_topic_id=14,
        enable_topic_id=15,
        owner_telegram_user_id="42",
        owner_username="owner",
        owner_first_name="Иван",
        poller_heartbeat_at=datetime(2026, 3, 30, 11, 30, tzinfo=UTC),
    )
    mock_db.scalar = AsyncMock(return_value=row)

    with (
        patch("apps.api.routers.settings.decrypt", return_value="1234567890TOKEN"),
        patch(
            "apps.api.routers.settings.get_latest_active_invite", new=AsyncMock(return_value=invite)
        ),
        patch("apps.api.routers.settings.poller_status_from_settings", return_value="ONLINE"),
    ):
        result = await get_telegram_settings(db=mock_db)

    assert result.delivery_mode == TelegramDeliveryMode.FORUM_GROUP.value
    assert result.forum_chat_id == "-1003701505954"
    assert result.chat_id == "-1003701505954"
    assert result.control_topic_id == 11
    assert result.warning_topic_id == 13
    assert result.stop_topic_id == 14
    assert result.enable_topic_id == 15
    assert result.auth_deep_link == ""
    assert result.activation_command == "/start 123456"
    assert result.active_invite is not None
    assert result.active_invite.deep_link == ""
    assert result.active_invite.activation_command == "/start 654321"


# Проверяем, что установка токена сразу готовит forum cutover и возвращает команду активации.
@pytest.mark.asyncio
async def test_set_telegram_token_prepares_forum_cutover(mock_db):
    from apps.api.routers.settings import set_telegram_token
    from apps.api.schemas import TelegramForumCutoverResponseSchema, TelegramSetTokenRequest

    row = SimpleNamespace(
        singleton_key="default",
        bot_token_encrypted="",
        bot_username="",
    )
    response = MagicMock()
    response.json.return_value = {"ok": True, "result": {"username": "adguard_fb_bot"}}
    http_client = AsyncMock()
    http_client.get = AsyncMock(return_value=response)
    http_client.__aenter__.return_value = http_client
    http_client.__aexit__.return_value = False
    cutover = TelegramForumCutoverResponseSchema(
        bot_username="adguard_fb_bot",
        chat_id="-1003701505954",
        auth_code="123456",
        activation_command="/start 123456",
        control_topic_id=11,
        warning_topic_id=13,
        stop_topic_id=14,
        enable_topic_id=15,
        forum_cutover_status="WAITING_OWNER_AUTH",
        message="Forum topics готовы.",
    )

    with (
        patch("httpx.AsyncClient", return_value=http_client),
        patch(
            "apps.api.routers.settings.get_or_create_telegram_settings",
            new=AsyncMock(return_value=row),
        ),
        patch("apps.api.routers.settings.encrypt", return_value="enc-token"),
        patch(
            "apps.api.routers.settings._prepare_telegram_forum_cutover",
            new=AsyncMock(return_value=cutover),
        ) as prepare_cutover,
    ):
        result = await set_telegram_token(
            body=TelegramSetTokenRequest(bot_token="123456:ABC"),
            db=mock_db,
        )

    assert row.bot_token_encrypted == "enc-token"
    assert row.bot_username == "adguard_fb_bot"
    mock_db.flush.assert_awaited_once()
    prepare_cutover.assert_awaited_once()
    assert result["auth_deep_link"] == ""
    assert result["activation_command"] == "/start 123456"
    assert result["control_topic_id"] == 11


# Проверяем, что cutover endpoint отказывается работать без сохранённого токена.
@pytest.mark.asyncio
async def test_prepare_telegram_forum_cutover_requires_saved_token(mock_db):
    from apps.api.routers.settings import prepare_telegram_forum_cutover

    mock_db.scalar = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc_info:
        await prepare_telegram_forum_cutover(db=mock_db)

    assert exc_info.value.status_code == 400
    assert "не настроен" in exc_info.value.detail


# Проверяем, что инвайт в forum-режиме отдаёт только команду активации без deep link.
@pytest.mark.asyncio
async def test_create_invite_code_returns_activation_command_for_forum_group(mock_db):
    from apps.api.routers.vision_telegram import create_invite_code

    row = SimpleNamespace(
        singleton_key="default",
        is_authorized=True,
        bot_username="adguard_fb_bot",
        owner_telegram_user_id="42",
        owner_username="owner",
        delivery_mode=TelegramDeliveryMode.FORUM_GROUP.value,
        control_topic_id=11,
        warning_topic_id=13,
        stop_topic_id=14,
        enable_topic_id=15,
    )
    invite = SimpleNamespace(
        code="654321",
        role=TelegramUserRole.RECIPIENT.value,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    mock_db.scalar = AsyncMock(return_value=row)

    with patch(
        "apps.api.routers.vision_telegram.create_telegram_invite",
        new=AsyncMock(return_value=invite),
    ):
        result = await create_invite_code(db=mock_db)

    mock_db.commit.assert_awaited_once()
    assert result.deep_link == ""
    assert result.activation_command == "/start 654321"
    assert result.activation_target == "CONTROL"

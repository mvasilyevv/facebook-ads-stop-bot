# -*- coding: utf-8 -*-
"""Тесты API для Telegram настроек."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.domain import TelegramUserRole


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


# Проверяем, что endpoint мягкой проверки CDP зарегистрирован для bootstrap из run.sh.
def test_vision_ensure_cdp_route_is_registered():
    from apps.api.main import app

    methods = set()
    for route in app.routes:
        if getattr(route, "path", "") == "/api/vision/ensure-cdp":
            methods.update(getattr(route, "methods", set()))

    assert "POST" in methods


# Проверяем, что GET настроек Telegram отдаёт поля и activation command.
@pytest.mark.asyncio
async def test_get_telegram_settings_returns_basic_fields(mock_db):
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
        owner_telegram_user_id="42",
        owner_username="owner",
        owner_first_name="Иван",
        poller_heartbeat_at=datetime(2026, 3, 30, 11, 30, tzinfo=UTC),
        web_app_url="",
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

    assert result.chat_id == "-1003701505954"
    assert result.activation_command == "/start 123456"
    assert result.active_invite is not None
    assert result.active_invite.activation_command == "/start 654321"


# Проверяем, что инвайт для нового получателя возвращает activation_command без deep link.
@pytest.mark.asyncio
async def test_create_invite_code_returns_activation_command(mock_db):
    from apps.api.routers.vision_telegram import create_invite_code

    row = SimpleNamespace(
        singleton_key="default",
        is_authorized=True,
        bot_username="adguard_fb_bot",
        owner_telegram_user_id="42",
        owner_username="owner",
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
    assert result.activation_command == "/start 654321"

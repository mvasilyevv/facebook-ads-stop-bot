# -*- coding: utf-8 -*-
"""Тесты API для web_app_url в Telegram настройках."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def mock_db():
    """Мок async DB-сессии."""
    db = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


# Проверяем, что PUT с валидным HTTPS URL сохраняет значение и возвращает 200.
@pytest.mark.asyncio
async def test_set_web_app_url_valid(mock_db):
    from apps.api.routers.settings import WebAppUrlRequest, set_web_app_url

    row = SimpleNamespace(web_app_url=None)

    with patch(
        "apps.api.routers.settings.get_or_create_telegram_settings",
        new=AsyncMock(return_value=row),
    ):
        result = await set_web_app_url(
            body=WebAppUrlRequest(web_app_url="https://example.com/tma/"),
            db=mock_db,
        )

    assert result == {"ok": True, "web_app_url": "https://example.com/tma/"}
    assert row.web_app_url == "https://example.com/tma/"
    mock_db.commit.assert_awaited_once()


# Проверяем, что PUT с пустой строкой очищает поле (None в БД).
@pytest.mark.asyncio
async def test_set_web_app_url_empty_clears(mock_db):
    from apps.api.routers.settings import WebAppUrlRequest, set_web_app_url

    row = SimpleNamespace(web_app_url="https://old.example.com/")

    with patch(
        "apps.api.routers.settings.get_or_create_telegram_settings",
        new=AsyncMock(return_value=row),
    ):
        result = await set_web_app_url(
            body=WebAppUrlRequest(web_app_url=""),
            db=mock_db,
        )

    assert result == {"ok": True, "web_app_url": ""}
    assert row.web_app_url is None


# Проверяем, что HTTP URL отклоняется с кодом 400.
@pytest.mark.asyncio
async def test_set_web_app_url_rejects_http(mock_db):
    from fastapi import HTTPException

    from apps.api.routers.settings import WebAppUrlRequest, set_web_app_url

    with pytest.raises(HTTPException) as exc_info:
        await set_web_app_url(
            body=WebAppUrlRequest(web_app_url="http://insecure.example.com"),
            db=mock_db,
        )

    assert exc_info.value.status_code == 400


# Проверяем, что GET /settings/telegram возвращает поле web_app_url.
@pytest.mark.asyncio
async def test_get_telegram_settings_includes_web_app_url(mock_db):
    from apps.api.routers.settings import get_telegram_settings

    row = SimpleNamespace(
        singleton_key="default",
        bot_token_encrypted="enc",
        chat_id="123",
        is_authorized=True,
        bot_username="testbot",
        auth_code="",
        owner_telegram_user_id=None,
        owner_username=None,
        owner_first_name=None,
        poller_heartbeat_at=None,
        web_app_url="https://example.com/tma/",
    )
    mock_db.scalar = AsyncMock(return_value=row)

    with (
        patch("apps.api.routers.settings.decrypt", return_value="TOKEN"),
        patch(
            "apps.api.routers.settings.get_latest_active_invite",
            new=AsyncMock(return_value=None),
        ),
        patch("apps.api.routers.settings.poller_status_from_settings", return_value="ONLINE"),
        patch("apps.api.routers.settings._serialize_primary_recipient", return_value=None),
    ):
        result = await get_telegram_settings(db=mock_db)

    assert hasattr(result, "web_app_url")
    assert result.web_app_url == "https://example.com/tma/"

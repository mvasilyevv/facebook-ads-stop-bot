# -*- coding: utf-8 -*-
"""Unit-тесты для load_web_app_url."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_db_url_takes_priority_over_env():
    # URL из БД должен иметь приоритет над значением из .env
    row = MagicMock()
    row.web_app_url = "https://from-db.example/"

    mock_session = AsyncMock()
    mock_session.scalar = AsyncMock(return_value=row)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock(return_value=mock_session)

    mock_settings = MagicMock()
    mock_settings.web_app_url = "https://env.example/"

    with (
        patch("core.telegram.service.get_session_factory", return_value=mock_factory),
        patch("core.telegram.service.get_settings", return_value=mock_settings),
    ):
        from core.telegram.service import load_web_app_url

        result = await load_web_app_url()

    assert result == "https://from-db.example/"


@pytest.mark.asyncio
async def test_fallback_to_env_when_db_empty():
    # Если в БД web_app_url пустой или None — возвращается значение из settings
    row = MagicMock()
    row.web_app_url = None

    mock_session = AsyncMock()
    mock_session.scalar = AsyncMock(return_value=row)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock(return_value=mock_session)

    mock_settings = MagicMock()
    mock_settings.web_app_url = "https://env.example/"

    with (
        patch("core.telegram.service.get_session_factory", return_value=mock_factory),
        patch("core.telegram.service.get_settings", return_value=mock_settings),
    ):
        from core.telegram.service import load_web_app_url

        result = await load_web_app_url()

    assert result == "https://env.example/"


@pytest.mark.asyncio
async def test_fallback_to_env_when_db_raises():
    # Если БД упала с исключением — возвращается URL из settings, debug-лог не поднимает ошибку
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(side_effect=RuntimeError("db down"))
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock(return_value=mock_session)

    mock_settings = MagicMock()
    mock_settings.web_app_url = "https://env-fallback.example/"

    with (
        patch("core.telegram.service.get_session_factory", return_value=mock_factory),
        patch("core.telegram.service.get_settings", return_value=mock_settings),
    ):
        from core.telegram.service import load_web_app_url

        result = await load_web_app_url()

    assert result == "https://env-fallback.example/"

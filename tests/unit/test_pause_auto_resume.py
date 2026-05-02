# -*- coding: utf-8 -*-
"""Тесты авто-resume сканирования в observer_worker при истечении pause_until."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Проверяем, что авто-resume включает сканирование если pause_until в прошлом.
@pytest.mark.asyncio
async def test_auto_resume_when_pause_until_expired():
    """_maybe_auto_resume_scanning: если pause_until <= now → is_scanning_enabled=True."""
    from apps.observer_worker.main import _maybe_auto_resume_scanning

    mock_settings = MagicMock()
    mock_settings.pause_until = datetime.now(UTC) - timedelta(minutes=5)
    mock_settings.is_scanning_enabled = False

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("apps.observer_worker.main.get_session_factory", return_value=mock_factory),
        patch(
            "core.settings_queries.get_or_create_observer_settings",
            new_callable=AsyncMock,
            return_value=mock_settings,
        ),
    ):
        await _maybe_auto_resume_scanning()

    assert mock_settings.is_scanning_enabled is True
    assert mock_settings.pause_until is None
    mock_session.commit.assert_called_once()


# Проверяем, что авто-resume НЕ включает сканирование если pause_until ещё не истёк.
@pytest.mark.asyncio
async def test_auto_resume_skips_when_pause_until_in_future():
    """_maybe_auto_resume_scanning: если pause_until > now → ничего не меняется."""
    from apps.observer_worker.main import _maybe_auto_resume_scanning

    mock_settings = MagicMock()
    mock_settings.pause_until = datetime.now(UTC) + timedelta(minutes=30)
    mock_settings.is_scanning_enabled = False

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("apps.observer_worker.main.get_session_factory", return_value=mock_factory),
        patch(
            "core.settings_queries.get_or_create_observer_settings",
            new_callable=AsyncMock,
            return_value=mock_settings,
        ),
    ):
        await _maybe_auto_resume_scanning()

    # Сканирование не должно быть включено
    assert mock_settings.is_scanning_enabled is False
    mock_session.commit.assert_not_called()


# Проверяем, что авто-resume не трогает настройки если pause_until равно None.
@pytest.mark.asyncio
async def test_auto_resume_skips_when_pause_until_is_none():
    """_maybe_auto_resume_scanning: если pause_until=None → ничего не меняется."""
    from apps.observer_worker.main import _maybe_auto_resume_scanning

    mock_settings = MagicMock()
    mock_settings.pause_until = None
    mock_settings.is_scanning_enabled = False

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("apps.observer_worker.main.get_session_factory", return_value=mock_factory),
        patch(
            "core.settings_queries.get_or_create_observer_settings",
            new_callable=AsyncMock,
            return_value=mock_settings,
        ),
    ):
        await _maybe_auto_resume_scanning()

    mock_session.commit.assert_not_called()

# -*- coding: utf-8 -*-
"""Unit-тесты для core/telegram/settings_compute.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# --- compute_is_authorized ---


# Если config=None — не авторизован
def test_compute_is_authorized_none_config() -> None:
    from core.telegram.settings_compute import compute_is_authorized

    assert compute_is_authorized(None) is False


# Если bot_token_encrypted пустая строка — не авторизован
def test_compute_is_authorized_empty_token() -> None:
    from core.telegram.settings_compute import compute_is_authorized

    config = SimpleNamespace(bot_token_encrypted="")
    assert compute_is_authorized(config) is False


# Если bot_token_encrypted заполнен — авторизован
def test_compute_is_authorized_has_token() -> None:
    from core.telegram.settings_compute import compute_is_authorized

    config = SimpleNamespace(bot_token_encrypted="gAAAAAB...")
    assert compute_is_authorized(config) is True


# bot_token_encrypted=None → не авторизован
def test_compute_is_authorized_token_is_none() -> None:
    from core.telegram.settings_compute import compute_is_authorized

    config = SimpleNamespace(bot_token_encrypted=None)
    assert compute_is_authorized(config) is False


# --- compute_poller_status ---


# Если config=None — OFFLINE
@pytest.mark.asyncio
async def test_compute_poller_status_none_config() -> None:
    from core.telegram.settings_compute import compute_poller_status

    result = await compute_poller_status(None)
    assert result == "OFFLINE"


# Если heartbeat=None — OFFLINE
@pytest.mark.asyncio
async def test_compute_poller_status_no_heartbeat() -> None:
    from core.telegram.settings_compute import compute_poller_status

    config = SimpleNamespace(poller_heartbeat_at=None)
    result = await compute_poller_status(config)
    assert result == "OFFLINE"


# Heartbeat 30 секунд назад — ONLINE
@pytest.mark.asyncio
async def test_compute_poller_status_online() -> None:
    from core.telegram.settings_compute import compute_poller_status

    heartbeat = datetime.now(UTC) - timedelta(seconds=30)
    config = SimpleNamespace(poller_heartbeat_at=heartbeat)
    result = await compute_poller_status(config)
    assert result == "ONLINE"


# Heartbeat 90 секунд назад — OFFLINE (превысил порог 60с)
@pytest.mark.asyncio
async def test_compute_poller_status_expired_heartbeat() -> None:
    from core.telegram.settings_compute import compute_poller_status

    heartbeat = datetime.now(UTC) - timedelta(seconds=90)
    config = SimpleNamespace(poller_heartbeat_at=heartbeat)
    result = await compute_poller_status(config)
    assert result == "OFFLINE"


# 59 секунд назад — ONLINE (чуть ниже порога 60с)
@pytest.mark.asyncio
async def test_compute_poller_status_just_below_threshold() -> None:
    from core.telegram.settings_compute import compute_poller_status

    heartbeat = datetime.now(UTC) - timedelta(seconds=59)
    config = SimpleNamespace(poller_heartbeat_at=heartbeat)
    result = await compute_poller_status(config)
    assert result == "ONLINE"


# Naive datetime — должен обработать без ошибки
@pytest.mark.asyncio
async def test_compute_poller_status_naive_datetime() -> None:
    from core.telegram.settings_compute import compute_poller_status

    # Naive datetime — coerce к UTC
    heartbeat = datetime.utcnow() - timedelta(seconds=10)
    config = SimpleNamespace(poller_heartbeat_at=heartbeat)
    result = await compute_poller_status(config)
    assert result == "ONLINE"


# --- compute_bot_username ---


# Если config=None — возвращает None без обращения к Redis
@pytest.mark.asyncio
async def test_compute_bot_username_none_config() -> None:
    from core.telegram.settings_compute import compute_bot_username

    redis = AsyncMock()
    result = await compute_bot_username(None, redis)
    assert result is None
    redis.get.assert_not_called()


# Redis кэш-хит: возвращает кэшированное имя без вызова httpx
@pytest.mark.asyncio
async def test_compute_bot_username_redis_cache_hit() -> None:
    from core.telegram.settings_compute import compute_bot_username

    redis = AsyncMock()
    redis.get.return_value = "mybot"

    config = SimpleNamespace(bot_token_encrypted="gAAAAAB...")
    result = await compute_bot_username(config, redis)
    assert result == "mybot"


# Redis кэш-miss, httpx успешно возвращает username и кэширует
@pytest.mark.asyncio
async def test_compute_bot_username_redis_miss_httpx_ok() -> None:
    from core.telegram.settings_compute import compute_bot_username

    redis = AsyncMock()
    redis.get.return_value = None  # кэш пустой

    config = SimpleNamespace(bot_token_encrypted="gAAAAAB...")

    mock_response = MagicMock()
    mock_response.json.return_value = {"result": {"username": "testbot"}}
    mock_response.raise_for_status = MagicMock()

    with (
        patch("core.telegram.settings_compute.compute_is_authorized", return_value=True),
        patch("core.crypto.decrypt", return_value="real_token"),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_http = AsyncMock()
        mock_http.get.return_value = mock_response
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await compute_bot_username(config, redis)

    assert result == "testbot"
    # Кэш должен быть обновлён
    redis.set.assert_called_once()


# httpx выбрасывает ошибку — возвращает None, не пробрасывает
@pytest.mark.asyncio
async def test_compute_bot_username_httpx_error_returns_none() -> None:
    import httpx

    from core.telegram.settings_compute import compute_bot_username

    redis = AsyncMock()
    redis.get.return_value = None

    config = SimpleNamespace(bot_token_encrypted="gAAAAAB...")

    with (
        patch("core.telegram.settings_compute.compute_is_authorized", return_value=True),
        patch("core.crypto.decrypt", return_value="real_token"),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_http = AsyncMock()
        mock_http.get.side_effect = httpx.RequestError("connection failed")
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await compute_bot_username(config, redis)

    assert result is None


# --- compute_auth_deep_link ---


# Если username None — возвращает None
def test_compute_auth_deep_link_none_username() -> None:
    from core.telegram.settings_compute import compute_auth_deep_link

    assert compute_auth_deep_link(None, "OWNER123") is None


# Если username задан — правильный deep-link
def test_compute_auth_deep_link_with_username() -> None:
    from core.telegram.settings_compute import compute_auth_deep_link

    result = compute_auth_deep_link("@mybot", "OWNER123")
    assert result == "https://t.me/mybot?start=OWNER123"


# Пустая строка как username — возвращает None
def test_compute_auth_deep_link_empty_username() -> None:
    from core.telegram.settings_compute import compute_auth_deep_link

    assert compute_auth_deep_link("", "OWNER123") is None


def test_compute_auth_deep_link_without_invite() -> None:
    from core.telegram.settings_compute import compute_auth_deep_link

    assert compute_auth_deep_link("mybot", None) is None


# --- compute_activation_command ---


# Команда содержит тот же invite-код, что и deep-link
def test_compute_activation_command_with_invite() -> None:
    from core.telegram.settings_compute import compute_activation_command

    assert compute_activation_command("OWNER123") == "/start OWNER123"


def test_compute_activation_command_without_invite() -> None:
    from core.telegram.settings_compute import compute_activation_command

    assert compute_activation_command(None) is None

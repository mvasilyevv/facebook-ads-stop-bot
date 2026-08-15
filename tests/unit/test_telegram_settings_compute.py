# -*- coding: utf-8 -*-
"""Unit-тесты для core/telegram/settings_compute.py."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

_FINGERPRINT = "a" * 64


@asynccontextmanager
async def _authority(authorized: bool):
    yield authorized


def _configured_snapshot() -> SimpleNamespace:
    return SimpleNamespace(
        bot_token_encrypted="gAAAAAB...",
        is_enabled=True,
        webhook_generation=7,
        credential_fingerprint=_FINGERPRINT,
    )


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


def test_compute_is_authorized_disabled_token_is_not_active() -> None:
    from core.telegram.settings_compute import compute_is_authorized

    config = SimpleNamespace(bot_token_encrypted="gAAAAAB...", is_enabled=False)
    assert compute_is_authorized(config) is False


# bot_token_encrypted=None → не авторизован
def test_compute_is_authorized_token_is_none() -> None:
    from core.telegram.settings_compute import compute_is_authorized

    config = SimpleNamespace(bot_token_encrypted=None)
    assert compute_is_authorized(config) is False


# --- compute_bot_username ---


# Если config=None — возвращает explicit unknown без внешнего запроса.
@pytest.mark.asyncio
async def test_compute_bot_username_none_config() -> None:
    from core.telegram.settings_compute import compute_bot_username

    result = await compute_bot_username(None, engine=object())  # type: ignore[arg-type]
    assert result is None


# Gateway успешно возвращает канонический username.
@pytest.mark.asyncio
async def test_compute_bot_username_gateway_ok() -> None:
    from core.telegram.settings_compute import compute_bot_username

    config = _configured_snapshot()
    engine = object()

    with (
        patch("core.telegram.settings_compute.compute_is_authorized", return_value=True),
        patch("core.crypto.decrypt", return_value="real_token"),
        patch("core.telegram.gateway.TelegramHTMLGateway") as gateway_cls,
        patch(
            "core.telegram.outbound_authority.hold_telegram_outbound_authority",
            side_effect=lambda *_args, **_kwargs: _authority(True),
        ),
    ):
        gateway = gateway_cls.return_value
        gateway.credential_fingerprint = _FINGERPRINT
        gateway.get_me = AsyncMock(return_value={"username": "testbot"})
        gateway.close = AsyncMock()

        result = await compute_bot_username(config, engine=engine)  # type: ignore[arg-type]

    assert result == "testbot"
    gateway.close.assert_awaited_once()


# Gateway выбрасывает ошибку — возвращает None, не пробрасывает
@pytest.mark.asyncio
async def test_compute_bot_username_httpx_error_returns_none() -> None:
    from core.telegram.settings_compute import compute_bot_username

    config = _configured_snapshot()

    with (
        patch("core.telegram.settings_compute.compute_is_authorized", return_value=True),
        patch("core.crypto.decrypt", return_value="real_token"),
        patch("core.telegram.gateway.TelegramHTMLGateway") as gateway_cls,
        patch(
            "core.telegram.outbound_authority.hold_telegram_outbound_authority",
            side_effect=lambda *_args, **_kwargs: _authority(True),
        ),
    ):
        gateway = gateway_cls.return_value
        gateway.credential_fingerprint = _FINGERPRINT
        gateway.get_me = AsyncMock(side_effect=RuntimeError("connection failed"))
        gateway.close = AsyncMock()

        result = await compute_bot_username(config, engine=object())  # type: ignore[arg-type]

    assert result is None
    gateway.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_compute_bot_username_never_logs_decrypt_exception_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from core.telegram.settings_compute import compute_bot_username

    config = _configured_snapshot()
    credential_marker = "123456789:do-not-log-this-token-material"

    with (
        patch("core.telegram.settings_compute.compute_is_authorized", return_value=True),
        patch(
            "core.crypto.decrypt",
            side_effect=RuntimeError(credential_marker),
        ),
    ):
        result = await compute_bot_username(config, engine=object())  # type: ignore[arg-type]

    assert result is None
    assert credential_marker not in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_compute_bot_username_denied_authority_never_calls_get_me() -> None:
    from core.telegram.settings_compute import compute_bot_username

    config = _configured_snapshot()
    engine = object()
    with (
        patch("core.crypto.decrypt", return_value="real_token"),
        patch("core.telegram.gateway.TelegramHTMLGateway") as gateway_cls,
        patch(
            "core.telegram.outbound_authority.hold_telegram_outbound_authority",
            side_effect=lambda *_args, **_kwargs: _authority(False),
        ) as hold,
    ):
        gateway = gateway_cls.return_value
        gateway.credential_fingerprint = _FINGERPRINT
        gateway.get_me = AsyncMock()
        gateway.close = AsyncMock()

        result = await compute_bot_username(config, engine=engine)  # type: ignore[arg-type]

    assert result is None
    gateway.get_me.assert_not_awaited()
    gateway.close.assert_awaited_once()
    hold.assert_called_once_with(
        engine,
        bot_generation=7,
        credential_fingerprint=_FINGERPRINT,
    )


# --- compute_auth_deep_link ---


# Если username None — возвращает None
def test_compute_auth_deep_link_none_username() -> None:
    from core.telegram.settings_compute import compute_auth_deep_link

    assert compute_auth_deep_link(None, "OWNER123") is None


# Если username задан — правильный deep-link
def test_compute_auth_deep_link_with_username() -> None:
    from core.telegram.settings_compute import compute_auth_deep_link

    result = compute_auth_deep_link("@mybot", "OWNER123")
    assert result == "https://t.me/mybot"
    assert "OWNER123" not in result
    assert "?" not in result


# Пустая строка как username — возвращает None
def test_compute_auth_deep_link_empty_username() -> None:
    from core.telegram.settings_compute import compute_auth_deep_link

    assert compute_auth_deep_link("", "OWNER123") is None


def test_compute_auth_deep_link_without_invite() -> None:
    from core.telegram.settings_compute import compute_auth_deep_link

    assert compute_auth_deep_link("mybot", None) is None


# --- compute_activation_command ---


# Invite-код передаётся только в команде, не в URL.
def test_compute_activation_command_with_invite() -> None:
    from core.telegram.settings_compute import compute_activation_command

    assert compute_activation_command("OWNER123") == "/start OWNER123"


def test_compute_activation_command_without_invite() -> None:
    from core.telegram.settings_compute import compute_activation_command

    assert compute_activation_command(None) is None

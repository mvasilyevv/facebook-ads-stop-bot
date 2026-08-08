from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

from apps.api.routers.ws import (
    _validate_tma_websocket_session,
    _websocket_api_key,
    _websocket_tma_session,
)
from core.auth.tma import issue_session_token


def test_websocket_prefers_caddy_injected_header() -> None:
    websocket = SimpleNamespace(
        headers={"x-api-key": "server-only"},
        query_params={"api_key": "legacy-query"},
    )

    assert _websocket_api_key(websocket) == "server-only"


def test_websocket_rejects_query_string_credentials() -> None:
    websocket = SimpleNamespace(headers={}, query_params={"api_key": "direct-client"})

    assert _websocket_api_key(websocket) == ""


def test_websocket_missing_credentials_is_empty() -> None:
    websocket = SimpleNamespace(headers={}, query_params={})

    assert _websocket_api_key(websocket) == ""


def test_websocket_tma_session_uses_subprotocol_not_query_string() -> None:
    websocket = SimpleNamespace(
        headers={"sec-websocket-protocol": "fb-operator-v1, tma.signed.session"},
        query_params={"tma_session": "must-not-be-read"},
    )

    assert _websocket_tma_session(websocket) == "signed.session"


@pytest.mark.asyncio
async def test_websocket_tma_session_uses_dedicated_secret_and_active_recipient(
    monkeypatch,
) -> None:
    import core.db as db
    import core.telegram.service as telegram_service

    secret = "test-tma-session-secret"
    token = issue_session_token("123", 3600, secret, bot_generation=1)
    engine = MagicMock()
    recipient_lookup = AsyncMock(return_value=SimpleNamespace(role="owner", is_owner=lambda: True))
    monkeypatch.setattr(db, "get_engine", lambda: engine)
    monkeypatch.setattr(
        telegram_service,
        "find_recipient_by_telegram_user_id",
        recipient_lookup,
    )
    monkeypatch.setattr(
        telegram_service,
        "telegram_generation_is_authoritative",
        AsyncMock(return_value=True),
    )
    settings = SimpleNamespace(
        tma_session_secret=SecretStr(secret),
        encryption_key=SecretStr("must-not-be-used"),
        tma_session_ttl_seconds=3600,
    )

    assert await _validate_tma_websocket_session(token, settings) is True
    recipient_lookup.assert_awaited_once_with(engine, telegram_user_id=123)


@pytest.mark.asyncio
async def test_websocket_tma_session_rejects_active_non_owner_recipient(
    monkeypatch,
) -> None:
    import core.db as db
    import core.telegram.service as telegram_service

    secret = "test-tma-session-secret"
    token = issue_session_token("123", 3600, secret, bot_generation=1)
    engine = MagicMock()
    recipient_lookup = AsyncMock(
        return_value=SimpleNamespace(role="recipient", is_owner=lambda: False)
    )
    monkeypatch.setattr(db, "get_engine", lambda: engine)
    monkeypatch.setattr(
        telegram_service,
        "find_recipient_by_telegram_user_id",
        recipient_lookup,
    )
    monkeypatch.setattr(
        telegram_service,
        "telegram_generation_is_authoritative",
        AsyncMock(return_value=True),
    )
    settings = SimpleNamespace(
        tma_session_secret=SecretStr(secret),
        encryption_key=SecretStr("unused"),
        tma_session_ttl_seconds=3600,
    )

    assert await _validate_tma_websocket_session(token, settings) is False
    recipient_lookup.assert_awaited_once_with(engine, telegram_user_id=123)


@pytest.mark.asyncio
async def test_websocket_tma_session_rejects_stale_bot_generation_before_recipient_lookup(
    monkeypatch,
) -> None:
    import core.db as db
    import core.telegram.service as telegram_service

    secret = "test-tma-session-secret"
    token = issue_session_token("123", 3600, secret, bot_generation=7)
    engine = MagicMock()
    recipient_lookup = AsyncMock()
    generation_check = AsyncMock(return_value=False)
    monkeypatch.setattr(db, "get_engine", lambda: engine)
    monkeypatch.setattr(
        telegram_service,
        "find_recipient_by_telegram_user_id",
        recipient_lookup,
    )
    monkeypatch.setattr(
        telegram_service,
        "telegram_generation_is_authoritative",
        generation_check,
    )
    settings = SimpleNamespace(
        tma_session_secret=SecretStr(secret),
        encryption_key=SecretStr("unused"),
        tma_session_ttl_seconds=3600,
    )

    assert await _validate_tma_websocket_session(token, settings) is False
    generation_check.assert_awaited_once_with(engine, bot_generation=7)
    recipient_lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_websocket_tma_session_never_falls_back_to_encryption_key() -> None:
    secret = "test-encryption-key"
    token = issue_session_token("123", 3600, secret, bot_generation=1)
    settings = SimpleNamespace(
        tma_session_secret=SecretStr(""),
        encryption_key=SecretStr(secret),
        tma_session_ttl_seconds=3600,
    )

    assert await _validate_tma_websocket_session(token, settings) is False


@pytest.mark.asyncio
async def test_websocket_tma_session_fails_closed_for_revoked_recipient(
    monkeypatch,
) -> None:
    import core.db as db
    import core.telegram.service as telegram_service

    secret = "test-tma-session-secret"
    token = issue_session_token("123", 3600, secret, bot_generation=1)
    monkeypatch.setattr(db, "get_engine", MagicMock())
    monkeypatch.setattr(
        telegram_service,
        "find_recipient_by_telegram_user_id",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        telegram_service,
        "telegram_generation_is_authoritative",
        AsyncMock(return_value=True),
    )
    settings = SimpleNamespace(
        tma_session_secret=SecretStr(secret),
        encryption_key=SecretStr("unused"),
        tma_session_ttl_seconds=3600,
    )

    assert await _validate_tma_websocket_session(token, settings) is False

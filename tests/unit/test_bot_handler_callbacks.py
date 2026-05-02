# -*- coding: utf-8 -*-
"""Тесты callback-обработчиков snooze и claim в bot_handler."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Проверяем, что _validate_alert_token возвращает True при совпадении токена.
@pytest.mark.asyncio
async def test_validate_alert_token_returns_true_when_token_matches():
    """_validate_alert_token должен вернуть True если open_state_token совпадает."""
    from core.telegram.bot_handler import _validate_alert_token

    mock_snapshot = MagicMock()

    session = AsyncMock()
    session.scalar = AsyncMock(return_value=mock_snapshot)

    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("core.telegram.bot_handler.get_session_factory", return_value=factory):
        result = await _validate_alert_token(fb_ad_id="111", token="valid_token")

    assert result is True


# Проверяем, что _validate_alert_token возвращает False если снэпшот не найден.
@pytest.mark.asyncio
async def test_validate_alert_token_returns_false_when_snapshot_missing():
    """_validate_alert_token должен вернуть False если снэпшот не найден в БД."""
    from core.telegram.bot_handler import _validate_alert_token

    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)

    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("core.telegram.bot_handler.get_session_factory", return_value=factory):
        result = await _validate_alert_token(fb_ad_id="999", token="any_token")

    assert result is False


# Проверяем, что _create_alert_snooze создаёт запись AlertSnooze в БД.
@pytest.mark.asyncio
async def test_create_alert_snooze_adds_record_to_db():
    """_create_alert_snooze должен добавить запись AlertSnooze в сессию и вернуть True."""
    from core.telegram.bot_handler import _create_alert_snooze

    session = AsyncMock()
    session.add = MagicMock()

    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("core.telegram.bot_handler.get_session_factory", return_value=factory):
        result = await _create_alert_snooze(fb_ad_id="123", minutes=30, tg_user_id="456")

    assert result is True
    session.add.assert_called_once()
    added_obj = session.add.call_args[0][0]
    assert added_obj.fb_ad_id == "123"
    assert added_obj.created_by_telegram_user_id == "456"
    # Убеждаемся, что snoozed_until > now
    assert added_obj.snoozed_until > datetime.now(UTC)


# Проверяем, что _claim_alert переводит FSM в состояние CLAIMED при валидном токене.
@pytest.mark.asyncio
async def test_claim_alert_transitions_to_claimed():
    """_claim_alert должен установить alert_state=CLAIMED и вернуть True при валидном токене."""
    from core.domain import AlertState
    from core.telegram.bot_handler import _claim_alert

    mock_snapshot = MagicMock()
    mock_snapshot.open_state_token = "good_token"
    mock_snapshot.alert_state = AlertState.WARNING_SENT

    session = AsyncMock()
    session.scalar = AsyncMock(return_value=mock_snapshot)

    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("core.telegram.bot_handler.get_session_factory", return_value=factory):
        result = await _claim_alert(fb_ad_id="111", token="good_token")

    assert result is True
    assert mock_snapshot.alert_state == AlertState.CLAIMED


# Проверяем, что _claim_alert возвращает False если снэпшот не найден (токен устарел).
@pytest.mark.asyncio
async def test_claim_alert_returns_false_when_snapshot_not_found():
    """_claim_alert должен вернуть False если снэпшот не найден по fb_ad_id+token."""
    from core.telegram.bot_handler import _claim_alert

    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)

    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("core.telegram.bot_handler.get_session_factory", return_value=factory):
        result = await _claim_alert(fb_ad_id="111", token="wrong_token")

    assert result is False

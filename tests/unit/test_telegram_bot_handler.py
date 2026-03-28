# -*- coding: utf-8 -*-
"""Тесты Telegram-контурa: авторизация, callback-guard и disable-логика."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.domain import AlertState


def _make_async_session(*, execute_side_effect=None, scalar_side_effect=None, scalar_return=None):
    """Создаёт мок async-сессии SQLAlchemy."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(side_effect=execute_side_effect)
    session.scalar = AsyncMock(side_effect=scalar_side_effect, return_value=scalar_return)
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


def _make_session_factory(session):
    """Создаёт фабрику, возвращающую один и тот же мок сессии."""
    return MagicMock(return_value=session)


def _scalar_result(obj):
    """Создаёт мок результата scalar_one_or_none()."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = obj
    return result


def _rows_result(rows):
    """Создаёт мок результата scalars().all()."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    return result


# Проверяем, что неавторизованный пользователь не может открыть обычную команду
@pytest.mark.asyncio
async def test_handle_update_blocks_unauthorized_command():
    """Неавторизованный chat_id должен получать только сообщение о входе через /start <код>."""
    from core.telegram import bot_handler

    session = _make_async_session(
        scalar_side_effect=[
            SimpleNamespace(is_authorized=False, chat_id=""),
            None,
        ]
    )
    factory = _make_session_factory(session)
    client = AsyncMock()

    with patch.object(bot_handler, "get_session_factory", return_value=factory):
        await bot_handler.handle_update(
            client,
            {"message": {"chat": {"id": "100"}, "text": "/ads"}},
        )

    client.send_message.assert_awaited_once()
    assert bot_handler.AUTH_REQUIRED_TEXT in client.send_message.await_args.kwargs["text"]
    client.edit_message.assert_not_awaited()


# Проверяем, что /start с кодом авторизует chat_id и не блокируется
@pytest.mark.asyncio
async def test_handle_update_authorizes_via_start_code():
    """/start <код> должен привязать chat_id и перевести пользователя в авторизованные."""
    from core.telegram import bot_handler

    settings_row = SimpleNamespace(
        singleton_key="default",
        chat_id="",
        is_authorized=False,
        auth_code="123456",
        pending_codes=[],
    )
    session = _make_async_session(execute_side_effect=[_scalar_result(settings_row)])
    factory = _make_session_factory(session)
    client = AsyncMock()

    with patch.object(bot_handler, "get_session_factory", return_value=factory):
        await bot_handler.handle_update(
            client,
            {
                "message": {
                    "chat": {"id": "100"},
                    "text": "/start 123456",
                    "from": {"first_name": "Иван", "username": "ivan"},
                }
            },
        )

    assert settings_row.chat_id == "100"
    assert settings_row.is_authorized is True
    assert settings_row.auth_code == ""
    client.send_message.assert_awaited_once()
    assert "Авторизация прошла успешно" in client.send_message.await_args.kwargs["text"]
    session.commit.assert_awaited_once()


# Проверяем, что callback без авторизации блокируется так же, как и команда
@pytest.mark.asyncio
async def test_handle_update_blocks_unauthorized_callback():
    """Неавторизованный callback должен только показать сообщение о входе."""
    from core.telegram import bot_handler

    session = _make_async_session(
        scalar_side_effect=[
            SimpleNamespace(is_authorized=False, chat_id=""),
            None,
        ]
    )
    factory = _make_session_factory(session)
    client = AsyncMock()

    with patch.object(bot_handler, "get_session_factory", return_value=factory):
        await bot_handler.handle_update(
            client,
            {
                "callback_query": {
                    "id": "cb-1",
                    "data": "cmd:ads",
                    "message": {"chat": {"id": "100"}, "message_id": 11},
                    "from": {"id": 7},
                }
            },
        )

    client.answer_callback_query.assert_awaited_once_with(
        "cb-1", text=bot_handler.AUTH_REQUIRED_TEXT
    )
    client.edit_message.assert_not_awaited()


# Проверяем, что STOP-снузер не применяется даже при прямом вызове helper-а
@pytest.mark.asyncio
async def test_snooze_alert_rejects_stop_alerts():
    """STOP-алерт не должен получать snoozed_until, потому что авто-отключение уже запущено."""
    from core.telegram.bot_handler import _snooze_alert

    stop_ad = SimpleNamespace(
        fb_ad_id="ad-1",
        open_state_token="token-1",
        alert_state=AlertState.STOP_SENT,
        ad_name="STOP ad",
        snoozed_until=None,
    )
    session = _make_async_session(scalar_return=stop_ad)
    factory = _make_session_factory(session)

    with patch("core.telegram.bot_handler.get_session_factory", return_value=factory):
        ad_name, applied = await _snooze_alert("token-1", 1)

    assert ad_name == "STOP ad"
    assert applied is False
    assert stop_ad.snoozed_until is None
    session.commit.assert_not_awaited()


# Проверяем, что массовое отключение берёт только текущий живой батч
@pytest.mark.asyncio
async def test_execute_disable_all_uses_current_live_batch_only():
    """Bulk disable должен брать только объявления из текущего скана, а не исторические хвосты."""
    from core.telegram import bot_handler

    current_ad = SimpleNamespace(fb_ad_id="current-ad")
    stale_ad = SimpleNamespace(fb_ad_id="stale-ad")
    live_last_scan = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)

    async def execute_side_effect(stmt):
        stmt_text = str(stmt)
        assert "last_observed_at" in stmt_text
        assert ">=" in stmt_text
        return _rows_result([current_ad])

    session = _make_async_session(
        execute_side_effect=execute_side_effect,
        scalar_return=live_last_scan,
    )
    factory = _make_session_factory(session)
    create_disable_task = AsyncMock(
        return_value={"fb_ad_id": current_ad.fb_ad_id, "ad_name": "Current"}
    )

    with (
        patch.object(bot_handler, "get_session_factory", return_value=factory),
        patch.object(bot_handler, "_create_disable_task", create_disable_task),
    ):
        count, failed = await bot_handler._execute_disable_all(tg_user_id="tg-1", username="tester")

    assert count == 1
    assert failed == 0
    create_disable_task.assert_awaited_once_with(
        snapshot_token=current_ad.fb_ad_id,
        tg_user_id="tg-1",
        username="tester",
    )
    assert stale_ad.fb_ad_id not in {
        call.kwargs["snapshot_token"] for call in create_disable_task.await_args_list
    }


# Проверяем, что ключ идемпотентности не зависит от экрана, с которого нажали disable
@pytest.mark.asyncio
async def test_create_disable_task_uses_stable_idempotency_key():
    """Один и тот же snapshot должен давать один и тот же idempotency_key вне зависимости от токена."""
    from core.telegram.bot_handler import _create_disable_task

    snapshot = SimpleNamespace(
        id="snapshot-123",
        open_state_token="token-abc",
        fb_ad_id="ad-123",
        offer_id="offer-1",
        ad_name="Тестовое объявление",
        alert_state=AlertState.STOP_SENT,
    )

    async def make_call(snapshot_token, execute_side_effect):
        session = _make_async_session(execute_side_effect=execute_side_effect, scalar_return=None)
        factory = _make_session_factory(session)
        with patch("core.telegram.bot_handler.get_session_factory", return_value=factory):
            result = await _create_disable_task(
                snapshot_token=snapshot_token,
                tg_user_id="tg-1",
                username="tester",
            )
        return session, result

    session_by_token, result_by_token = await make_call(
        "token-abc",
        [_scalar_result(snapshot)],
    )
    task_by_token = session_by_token.add.call_args.args[0]

    session_by_ad, result_by_ad = await make_call(
        "ad-123",
        [_scalar_result(None), _scalar_result(snapshot)],
    )
    task_by_ad = session_by_ad.add.call_args.args[0]

    assert result_by_token == {"fb_ad_id": "ad-123", "ad_name": "Тестовое объявление"}
    assert result_by_ad == {"fb_ad_id": "ad-123", "ad_name": "Тестовое объявление"}
    assert task_by_token.idempotency_key == "disable:snapshot-123"
    assert task_by_ad.idempotency_key == "disable:snapshot-123"
    assert task_by_token.open_state_token == "token-abc"
    assert task_by_ad.open_state_token == "token-abc"

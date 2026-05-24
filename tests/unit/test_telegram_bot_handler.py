# -*- coding: utf-8 -*-
"""Тесты Telegram-контура: авторизация, роли и disable-логика."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.domain import AlertState, TelegramUserRole


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
    session.delete = AsyncMock()
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
    """Создаёт мок результата scalars().unique().all() и scalars().all()."""
    result = MagicMock()
    scalars_mock = result.scalars.return_value
    scalars_mock.all.return_value = rows
    scalars_mock.unique.return_value.all.return_value = rows
    return result


# Проверяем, что private chat без авторизации получает текст AUTH_REQUIRED.
@pytest.mark.asyncio
async def test_handle_update_private_chat_no_access_sends_auth_required():
    """Личный чат без авторизации должен отправлять AUTH_REQUIRED текст."""
    from core.telegram import bot_handler

    client = AsyncMock()

    with patch.object(
        bot_handler,
        "resolve_telegram_access",
        new=AsyncMock(return_value=None),
    ):
        await bot_handler.handle_update(
            client,
            {
                "message": {
                    "chat": {"id": "100", "type": "private"},
                    "from": {"id": 7, "username": "tester"},
                    "text": "/start",
                },
            },
        )

    client.send_message.assert_awaited_once()
    assert bot_handler.AUTH_REQUIRED_TEXT in client.send_message.await_args.kwargs["text"]
    client.edit_message.assert_not_awaited()


# Проверяем, что /start с кодом авторизует владельца и открывает меню.
@pytest.mark.asyncio
async def test_handle_update_authorizes_via_start_code_and_returns_menu():
    """/start <код> должен привязать owner к чату и открыть меню."""
    from core.telegram import bot_handler

    settings_row = SimpleNamespace(
        singleton_key="default",
        chat_id="-1003701505954",
        is_authorized=False,
        auth_code="123456",
        owner_telegram_user_id="",
        owner_username="",
        owner_first_name="",
    )
    session = _make_async_session()
    factory = _make_session_factory(session)
    client = AsyncMock()

    with (
        patch.object(bot_handler, "get_session_factory", return_value=factory),
        patch.object(
            bot_handler,
            "get_or_create_telegram_settings",
            new=AsyncMock(return_value=settings_row),
        ),
        patch.object(
            bot_handler,
            "_render_start",
            new=AsyncMock(return_value=("Главное меню", {"inline_keyboard": []})),
        ),
    ):
        await bot_handler.handle_update(
            client,
            {
                "message": {
                    "chat": {"id": "-1003701505954", "type": "supergroup"},
                    "message_thread_id": 11,
                    "text": "/start 123456",
                    "from": {"id": 42, "first_name": "Иван", "username": "ivan"},
                },
            },
        )

    assert settings_row.chat_id == "-1003701505954"
    assert settings_row.is_authorized is True
    assert settings_row.auth_code == ""
    assert settings_row.owner_telegram_user_id == "42"
    assert settings_row.owner_username == "ivan"
    assert settings_row.owner_first_name == "Иван"
    session.commit.assert_awaited_once()
    client.send_message.assert_awaited_once()
    sent_text = client.send_message.await_args.kwargs["text"]
    assert "Авторизация прошла успешно" in sent_text


# Проверяем, что callback из old private chat даёт ответ без доступа.
@pytest.mark.asyncio
async def test_handle_update_private_callback_no_access():
    """Inline-кнопки из личного чата без авторизации должны возвращать отказ."""
    from core.telegram import bot_handler

    client = AsyncMock()

    await bot_handler.handle_update(
        client,
        {
            "callback_query": {
                "id": "cb-1",
                "data": "cmd:ads",
                "message": {"chat": {"id": "100", "type": "private"}, "message_id": 11},
                "from": {"id": 7},
            },
        },
    )

    client.answer_callback_query.assert_awaited_once_with("cb-1", text="Контур не активирован")
    client.edit_message.assert_not_awaited()


# Проверяем, что получатель не может менять настройки через /set.
@pytest.mark.asyncio
async def test_recipient_cannot_run_set_command():
    """Роль recipient должна получать owner-only отказ на /set."""
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.RECIPIENT.value)
    settings_row = SimpleNamespace()

    with (
        patch.object(
            bot_handler,
            "resolve_telegram_access",
            new=AsyncMock(return_value=access),
        ),
        patch.object(
            bot_handler,
            "_load_telegram_settings_row",
            new=AsyncMock(return_value=settings_row),
        ),
        patch.object(
            bot_handler,
            "_update_observer_setting",
            new=AsyncMock(),
        ) as update_setting,
    ):
        await bot_handler.handle_update(
            client,
            {
                "message": {
                    "chat": {"id": "-1003701505954", "type": "supergroup"},
                    "message_thread_id": 11,
                    "from": {"id": 7, "username": "guest"},
                    "text": "/set interval 60",
                },
            },
        )

    update_setting.assert_not_awaited()
    client.send_message.assert_awaited_once()
    assert bot_handler.OWNER_ONLY_TEXT in client.send_message.await_args.kwargs["text"]


# Проверяем, что владелец может менять настройки через /set.
@pytest.mark.asyncio
async def test_owner_can_run_set_command():
    """Роль owner должна успешно обновлять observer-настройку через /set."""
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)
    settings_row = SimpleNamespace()

    with (
        patch.object(
            bot_handler,
            "resolve_telegram_access",
            new=AsyncMock(return_value=access),
        ),
        patch.object(
            bot_handler,
            "_load_telegram_settings_row",
            new=AsyncMock(return_value=settings_row),
        ),
        patch.object(
            bot_handler,
            "_update_observer_setting",
            new=AsyncMock(),
        ) as update_setting,
    ):
        await bot_handler.handle_update(
            client,
            {
                "message": {
                    "chat": {"id": "-1003701505954", "type": "supergroup"},
                    "message_thread_id": 11,
                    "from": {"id": 1, "username": "owner"},
                    "text": "/set interval 60",
                },
            },
        )

    update_setting.assert_awaited_once_with(interval_seconds=60)
    client.send_message.assert_awaited_once()
    assert "Интервал обновления" in client.send_message.await_args.kwargs["text"]


# Проверяем, что неподдерживаемая команда возвращает понятный отказ.
@pytest.mark.asyncio
async def test_handle_update_unknown_command_sends_unsupported():
    """Команда /ads (нет обработчика) должна вернуть сообщение о неподдерживаемой команде."""
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)

    with patch.object(
        bot_handler,
        "resolve_telegram_access",
        new=AsyncMock(return_value=access),
    ):
        await bot_handler.handle_update(
            client,
            {
                "message": {
                    "chat": {"id": "-1003701505954", "type": "supergroup"},
                    "message_thread_id": 14,
                    "from": {"id": 1, "username": "owner"},
                    "text": "/ads",
                },
            },
        )

    client.send_message.assert_awaited_once()
    assert "не поддерживается" in client.send_message.await_args.kwargs["text"]


# Проверяем, что confirm-экран для отключения помнит исходное сообщение.
@pytest.mark.asyncio
async def test_stream_disable_confirm_keeps_origin_message_id():
    """Callback подтверждения должен сохранить id исходного алерта в confirm-flow."""
    from core.telegram import bot_handler

    client = AsyncMock()
    # OWNER, потому что disable-кнопки доступны только владельцу (auth-фикс B1).
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)

    with (
        patch.object(
            bot_handler,
            "resolve_telegram_access",
            new=AsyncMock(return_value=access),
        ),
        patch.object(
            bot_handler,
            "_render_disable_confirm",
            new=AsyncMock(return_value=("Подтверждение", {"inline_keyboard": []})),
        ) as render_confirm,
    ):
        await bot_handler.handle_update(
            client,
            {
                "callback_query": {
                    "id": "cb-1",
                    "data": "disable_confirm:snap-77",
                    "message": {
                        "chat": {"id": "-1003701505954", "type": "supergroup"},
                        "message_id": 41,
                        "message_thread_id": 12,
                    },
                    "from": {"id": 7, "username": "guest"},
                },
            },
        )

    render_confirm.assert_awaited_once_with(
        snapshot_token="snap-77",
        confirm_callback="disable_execute:snap-77:41",
        cancel_callback="confirm_cancel",
    )


# Проверяем, что подтверждённый disable обновляет и confirm, и исходный алерт.
@pytest.mark.asyncio
async def test_disable_execute_updates_origin_message_and_broadcasts_stop():
    """После подтверждения отключения исходный алерт должен получить короткий ack."""
    from core.telegram import bot_handler

    client = AsyncMock()
    # OWNER, потому что disable-кнопки доступны только владельцу (auth-фикс B1).
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)

    # campaign_name/adset_name убраны из возвращаемого словаря _create_disable_task
    task_info = {
        "fb_ad_id": "ad-1",
        "ad_name": "Рекламное объявление",
        "created_new": True,
        "incident_key": "incident-1",
        "message_context": SimpleNamespace(),
    }

    with (
        patch.object(
            bot_handler,
            "resolve_telegram_access",
            new=AsyncMock(return_value=access),
        ),
        patch.object(bot_handler, "_create_disable_task", new=AsyncMock(return_value=task_info)),
        patch.object(
            bot_handler,
            "_ack_disable_task_messages",
            new=AsyncMock(),
        ) as ack_messages,
        patch.object(
            bot_handler,
            "broadcast_disable_task_queue_message",
            new=AsyncMock(),
        ) as broadcast_queue,
    ):
        await bot_handler.handle_update(
            client,
            {
                "callback_query": {
                    "id": "cb-2",
                    "data": "disable_execute:snap-77:41",
                    "message": {
                        "chat": {"id": "-1003701505954", "type": "supergroup"},
                        "message_id": 52,
                        "message_thread_id": 12,
                    },
                    "from": {"id": 7, "username": "guest"},
                },
            },
        )

    ack_messages.assert_awaited_once()
    assert ack_messages.await_args.kwargs["origin_message_id"] == 41
    assert ack_messages.await_args.kwargs["current_message_id"] == 52
    assert (
        "Дальнейший статус смотрите в topic <b>STOP</b>" in ack_messages.await_args.kwargs["text"]
    )
    broadcast_queue.assert_awaited_once()


# Проверяем, что STOP-снузер не применяется даже при прямом вызове helper-а.
@pytest.mark.asyncio
async def test_snooze_alert_rejects_stop_alerts():
    """STOP-алерт не должен получать snoozed_until, потому что авто-отключение уже запущено."""
    from core.telegram.bot_handler import _snooze_alert

    # Мокируем JOIN-цепочку fb_ad → adset → campaign
    stop_ad = SimpleNamespace(
        fb_ad_id="ad-1",
        open_state_token="token-1",
        alert_state=AlertState.STOP_SENT,
        fb_ad=SimpleNamespace(ad_name="STOP ad", adset=None),
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


# Проверяем, что snooze теперь работает в минутах, а не в часах.
@pytest.mark.asyncio
async def test_snooze_alert_uses_minutes():
    """WARNING-алерт должен получать snoozed_until на указанное число минут."""
    from core.telegram.bot_handler import _snooze_alert

    # Мокируем JOIN-цепочку fb_ad → adset → campaign
    warning_ad = SimpleNamespace(
        fb_ad_id="ad-2",
        open_state_token="token-2",
        alert_state=AlertState.WARNING_SENT,
        fb_ad=SimpleNamespace(ad_name="WARNING ad", adset=None),
        snoozed_until=None,
    )
    session = _make_async_session(scalar_return=warning_ad)
    factory = _make_session_factory(session)
    before = datetime.now(UTC)

    with patch("core.telegram.bot_handler.get_session_factory", return_value=factory):
        ad_name, applied = await _snooze_alert("token-2", 30)

    assert ad_name == "WARNING ad"
    assert applied is True
    assert warning_ad.snoozed_until is not None
    assert warning_ad.snoozed_until >= before + timedelta(minutes=29)
    assert warning_ad.snoozed_until <= before + timedelta(minutes=31)
    session.commit.assert_awaited_once()


# Проверяем, что helper понимает legacy callback `:3` как 180 минут.
def test_normalize_snooze_minutes_supports_legacy_three_hours():
    """Старые callback-и со значением 3 должны превращаться в 180 минут."""
    from core.telegram.bot_handler import _normalize_snooze_minutes

    assert _normalize_snooze_minutes(3) == 180
    assert _normalize_snooze_minutes(30) == 30


# Проверяем, что ручной disable привязывается к ключу инцидента, а не к snapshot.id.
@pytest.mark.asyncio
async def test_create_disable_task_uses_stable_idempotency_key():
    """Один и тот же incident должен давать одинаковый manual-idempotency key."""
    from core.telegram.bot_handler import _create_disable_task

    # Мокируем JOIN-цепочку fb_ad → adset → campaign (offer_id убран из snapshot)
    snapshot = SimpleNamespace(
        id="snapshot-123",
        ad_id="ad-uuid-123",
        open_state_token="token-abc",
        fb_ad_id="ad-123",
        fb_ad=SimpleNamespace(
            ad_name="Тестовое объявление",
            adset=None,
        ),
        alert_state=AlertState.STOP_SENT,
        telegram_group_key=None,
        spend="0.00",
        clicks=0,
        cpc=None,
        outbound_clicks=0,
        outbound_ctr=None,
        landing_page_views=0,
        cost_per_landing_page_view=None,
        cpm=None,
        frequency=None,
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
        early_signal_rule_codes=[],
        warning_rule_codes=[],
        stop_rule_codes=["cpc_stop"],
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
        [_scalar_result(snapshot), _scalar_result(None), _scalar_result(None)],
    )
    task_by_token = session_by_token.add.call_args.args[0]

    session_by_ad, result_by_ad = await make_call(
        "ad-123",
        [
            _scalar_result(None),
            _scalar_result(snapshot),
            _scalar_result(None),
            _scalar_result(None),
        ],
    )
    task_by_ad = session_by_ad.add.call_args.args[0]

    # campaign_name/adset_name убраны из возвращаемого словаря
    assert result_by_token == {
        "fb_ad_id": "ad-123",
        "ad_name": "Тестовое объявление",
        "created_new": True,
        "incident_key": "token-abc",
        "message_context": result_by_token["message_context"],
    }
    assert result_by_ad == {
        "fb_ad_id": "ad-123",
        "ad_name": "Тестовое объявление",
        "created_new": True,
        "incident_key": "token-abc",
        "message_context": result_by_ad["message_context"],
    }
    assert task_by_token.idempotency_key == "manual:ad-123:token-abc"
    assert task_by_ad.idempotency_key == "manual:ad-123:token-abc"
    assert task_by_token.open_state_token == "token-abc"
    assert task_by_ad.open_state_token == "token-abc"


# Проверяем, что активная задача того же инцидента не плодит ручной дубль.
@pytest.mark.asyncio
async def test_create_disable_task_returns_existing_queue_state():
    """Если задача уже существует, helper должен вернуть created_new=False без новой записи."""
    from core.telegram.bot_handler import _create_disable_task

    # Мокируем JOIN-цепочку fb_ad → adset → campaign (offer_id убран из snapshot)
    snapshot = SimpleNamespace(
        id="snapshot-123",
        ad_id="ad-uuid-123",
        open_state_token="token-abc",
        fb_ad_id="ad-123",
        fb_ad=SimpleNamespace(
            ad_name="Тестовое объявление",
            adset=None,
        ),
        alert_state=AlertState.WARNING_SENT,
        telegram_group_key=None,
        spend="0.00",
        clicks=0,
        cpc=None,
        outbound_clicks=0,
        outbound_ctr=None,
        landing_page_views=0,
        cost_per_landing_page_view=None,
        cpm=None,
        frequency=None,
        leads=0,
        cost_per_lead=None,
        registrations=0,
        cost_per_registration=None,
        deposits=0,
        early_signal_rule_codes=[],
        warning_rule_codes=["cpl_stop"],
        stop_rule_codes=[],
    )
    existing_task = SimpleNamespace(id="task-1")

    session = _make_async_session(
        execute_side_effect=[
            _scalar_result(snapshot),
            _scalar_result(None),
            _scalar_result(None),
        ],
        scalar_side_effect=[existing_task],
    )
    factory = _make_session_factory(session)

    with patch("core.telegram.bot_handler.get_session_factory", return_value=factory):
        result = await _create_disable_task(
            snapshot_token="token-abc",
            tg_user_id="tg-1",
            username="tester",
        )

    # campaign_name/adset_name убраны из возвращаемого словаря
    assert result == {
        "fb_ad_id": "ad-123",
        "ad_name": "Тестовое объявление",
        "created_new": False,
        "incident_key": "token-abc",
        "message_context": result["message_context"],
    }
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


# Проверяем, что кнопку включения из рекомендации может нажать только owner.
@pytest.mark.asyncio
async def test_enable_recommendation_callback_is_owner_only():
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.RECIPIENT.value)

    with patch.object(
        bot_handler,
        "resolve_telegram_access",
        new=AsyncMock(return_value=access),
    ):
        await bot_handler.handle_update(
            client,
            {
                "callback_query": {
                    "id": "cb-10",
                    "data": "enable_reco:task:event-1",
                    "message": {
                        "chat": {"id": "-1003701505954", "type": "supergroup"},
                        "message_id": 11,
                        "message_thread_id": 15,
                    },
                    "from": {"id": 7, "username": "guest"},
                },
            },
        )

    client.send_message.assert_awaited_once()
    assert client.send_message.await_args.kwargs["chat_id"] == "-1003701505954"
    assert client.send_message.await_args.kwargs["message_thread_id"] == 15
    assert client.send_message.await_args.kwargs["text"] == bot_handler.OWNER_ONLY_TEXT


# Проверяем, что успешное создание EnableTask обновляет ENABLE-поток через broadcaster.
@pytest.mark.asyncio
async def test_enable_recommendation_callback_routes_to_enable_stream():
    """При успешном создании задачи callback не должен вручную редактировать текущее сообщение."""
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)
    safe_edit = AsyncMock()

    with (
        patch.object(
            bot_handler,
            "resolve_telegram_access",
            new=AsyncMock(return_value=access),
        ),
        patch.object(
            bot_handler,
            "_create_enable_task_from_recommendation",
            new=AsyncMock(
                return_value={
                    "outcome": "created",
                    "created_new": True,
                    "fb_ad_id": "ad-77",
                    "ad_name": "Enable Ad",
                    "detail": "ok",
                }
            ),
        ),
        patch.object(bot_handler, "_safe_edit_current_message", new=safe_edit),
        patch.object(
            bot_handler,
            "broadcast_enable_task_queue_message",
            new=AsyncMock(),
        ) as broadcast_mock,
    ):
        await bot_handler.handle_update(
            client,
            {
                "callback_query": {
                    "id": "cb-enable-created",
                    "data": "enable_reco:task:event-77",
                    "message": {
                        "chat": {"id": "-1003701505954", "type": "supergroup"},
                        "message_id": 21,
                        "message_thread_id": 15,
                    },
                    "from": {"id": 7, "username": "owner"},
                },
            },
        )

    safe_edit.assert_not_awaited()
    broadcast_mock.assert_awaited_once_with(
        ad_name="Enable Ad",
        fb_ad_id="ad-77",
        requested_by_username="owner",
        created_new=True,
        incident_key="event-77",
    )


# Проверяем, что устаревшая рекомендация не превращается в задачу и показывает понятный текст.
@pytest.mark.asyncio
async def test_enable_recommendation_callback_shows_stale_message():
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)
    safe_edit = AsyncMock()

    with (
        patch.object(
            bot_handler,
            "resolve_telegram_access",
            new=AsyncMock(return_value=access),
        ),
        patch.object(
            bot_handler,
            "_create_enable_task_from_recommendation",
            new=AsyncMock(
                return_value={
                    "outcome": "stale_batch",
                    "detail": "⚠️ Рекомендация устарела: объявление уже не входит в актуальный срез.",
                    "created_new": False,
                    "fb_ad_id": "ad-1",
                    "ad_name": "Ad 1",
                }
            ),
        ),
        patch.object(bot_handler, "_safe_edit_current_message", new=safe_edit),
        patch.object(
            bot_handler,
            "broadcast_enable_task_queue_message",
            new=AsyncMock(),
        ) as broadcast_mock,
    ):
        await bot_handler.handle_update(
            client,
            {
                "callback_query": {
                    "id": "cb-11",
                    "data": "enable_reco:task:event-2",
                    "message": {
                        "chat": {"id": "-1003701505954", "type": "supergroup"},
                        "message_id": 12,
                        "message_thread_id": 15,
                    },
                    "from": {"id": 7, "username": "owner"},
                },
            },
        )

    safe_edit.assert_awaited_once()
    assert "Рекомендация устарела" in safe_edit.await_args.kwargs["text"]
    broadcast_mock.assert_not_awaited()


# Проверяем, что рекомендация в stop-зоне не создаёт EnableTask и возвращает отказ.
@pytest.mark.asyncio
async def test_enable_recommendation_callback_shows_stop_reject():
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)
    safe_edit = AsyncMock()

    with (
        patch.object(
            bot_handler,
            "resolve_telegram_access",
            new=AsyncMock(return_value=access),
        ),
        patch.object(
            bot_handler,
            "_create_enable_task_from_recommendation",
            new=AsyncMock(
                return_value={
                    "outcome": "blocked_stop",
                    "detail": "⚠️ Рекомендация устарела: объявление сейчас уже в стоп-зоне.",
                    "created_new": False,
                    "fb_ad_id": "ad-2",
                    "ad_name": "Ad 2",
                }
            ),
        ),
        patch.object(bot_handler, "_safe_edit_current_message", new=safe_edit),
    ):
        await bot_handler.handle_update(
            client,
            {
                "callback_query": {
                    "id": "cb-12",
                    "data": "enable_reco:task:event-3",
                    "message": {
                        "chat": {"id": "-1003701505954", "type": "supergroup"},
                        "message_id": 13,
                        "message_thread_id": 15,
                    },
                    "from": {"id": 7, "username": "owner"},
                },
            },
        )

    safe_edit.assert_awaited_once()
    assert "стоп-зоне" in safe_edit.await_args.kwargs["text"]


# Проверяем, что confirm_cancel удаляет локальный confirm-flow без влияния на stream-цепочки.
@pytest.mark.asyncio
async def test_confirm_cancel_edits_current_message_to_cancelled_text():
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)
    safe_edit = AsyncMock()

    with (
        patch.object(
            bot_handler,
            "resolve_telegram_access",
            new=AsyncMock(return_value=access),
        ),
        patch.object(bot_handler, "_safe_edit_current_message", new=safe_edit),
    ):
        await bot_handler.handle_update(
            client,
            {
                "callback_query": {
                    "id": "cb-cancel",
                    "data": "confirm_cancel",
                    "message": {
                        "chat": {"id": "-1003701505954", "type": "supergroup"},
                        "message_id": 31,
                        "message_thread_id": 13,
                    },
                    "from": {"id": 7, "username": "owner"},
                },
            },
        )

    client.answer_callback_query.assert_awaited_once_with("cb-cancel", text="Отменено")
    safe_edit.assert_awaited_once()
    assert "Действие отменено" in safe_edit.await_args.kwargs["text"]


# Проверяем, что disable_execute из WARNING/EARLY обновляет только локальное сообщение и создаёт STOP lifecycle отдельно.
@pytest.mark.asyncio
async def test_disable_execute_keeps_local_ack_and_broadcasts_stop_stream():
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)
    safe_edit = AsyncMock()
    task_info = {
        "fb_ad_id": "ad-500",
        "ad_name": "Тестовый поток",
        "created_new": True,
        "incident_key": "incident-500",
        "message_context": SimpleNamespace(
            campaign_name="Campaign",
            adset_name="Adset",
            matched_rule_codes=["cpc_stop"],
            reason_title="Дорогой клик",
            reason_text="Цена клика превысила порог.",
            metrics_json={"spend": "10.00"},
        ),
    }

    with (
        patch.object(
            bot_handler,
            "resolve_telegram_access",
            new=AsyncMock(return_value=access),
        ),
        patch.object(
            bot_handler,
            "_create_disable_task",
            new=AsyncMock(return_value=task_info),
        ),
        patch.object(bot_handler, "_safe_edit_current_message", new=safe_edit),
        patch.object(
            bot_handler,
            "broadcast_disable_task_queue_message",
            new=AsyncMock(),
        ) as broadcast_mock,
    ):
        await bot_handler.handle_update(
            client,
            {
                "callback_query": {
                    "id": "cb-disable",
                    "data": "disable_execute:token-500",
                    "message": {
                        "chat": {"id": "-1003701505954", "type": "supergroup"},
                        "message_id": 41,
                        "message_thread_id": 13,
                    },
                    "from": {"id": 7, "username": "owner"},
                },
            },
        )

    safe_edit.assert_awaited_once()
    assert "topic <b>STOP</b>" in safe_edit.await_args.kwargs["text"]
    broadcast_mock.assert_awaited_once_with(
        ad_name="Тестовый поток",
        fb_ad_id="ad-500",
        requested_by_username="owner",
        created_new=True,
        incident_key="incident-500",
        context=task_info["message_context"],
    )


# Проверяем, что _try_authorize авторизует владельца по корректному коду.
@pytest.mark.asyncio
async def test_try_authorize_succeeds_with_correct_code():
    from core.telegram.bot_handler import _try_authorize

    settings_row = SimpleNamespace(
        chat_id="",
        is_authorized=False,
        auth_code="123456",
        owner_telegram_user_id="",
        owner_username="",
        owner_first_name="",
    )
    session = _make_async_session()
    factory = _make_session_factory(session)
    client = AsyncMock()

    with (
        patch("core.telegram.bot_handler.get_session_factory", return_value=factory),
        patch(
            "core.telegram.bot_handler.get_or_create_telegram_settings",
            new=AsyncMock(return_value=settings_row),
        ),
    ):
        handled = await _try_authorize(
            client,
            "-1003701505954",
            "123456",
            {"from": {"id": 42, "username": "ivan", "first_name": "Иван"}},
            message_thread_id=None,
        )

    assert handled is True
    assert settings_row.is_authorized is True
    assert settings_row.owner_telegram_user_id == "42"
    session.commit.assert_awaited_once()
    client.send_message.assert_awaited_once()
    assert "Авторизация прошла успешно" in client.send_message.await_args.kwargs["text"]


# Проверяем, что helper коммитит новую enable-задачу, если сервис вернул created.
@pytest.mark.asyncio
async def test_create_enable_task_from_recommendation_commits_created_task():
    from core.telegram.bot_handler import _create_enable_task_from_recommendation

    session = _make_async_session()
    factory = _make_session_factory(session)
    service_result = SimpleNamespace(
        outcome="created",
        fb_ad_id="ad-10",
        ad_name="Тест на включение",
        created_new=True,
        detail="✅ Создана задача на включение.",
        task_id="task-10",
        task_status="PENDING",
    )

    with (
        patch("core.telegram.bot_handler.get_session_factory", return_value=factory),
        patch(
            "core.telegram.bot_handler.promote_recommendation_to_enable_task",
            new=AsyncMock(return_value=service_result),
        ),
    ):
        result = await _create_enable_task_from_recommendation(
            recommendation_event_id="event-10",
            tg_user_id="tg-10",
            username="owner",
        )

    assert result == {
        "outcome": "created",
        "fb_ad_id": "ad-10",
        "ad_name": "Тест на включение",
        "created_new": True,
        "detail": "✅ Создана задача на включение.",
        "task_id": "task-10",
        "task_status": "PENDING",
    }
    session.commit.assert_awaited_once()


# Проверяем, что helper возвращает existing для уже созданной enable-задачи без потери текста.
@pytest.mark.asyncio
async def test_create_enable_task_from_recommendation_returns_existing_task():
    from core.telegram.bot_handler import _create_enable_task_from_recommendation

    session = _make_async_session()
    factory = _make_session_factory(session)
    service_result = SimpleNamespace(
        outcome="existing",
        fb_ad_id="ad-11",
        ad_name="Повторная задача",
        created_new=False,
        detail="ℹ️ Задача на включение уже была создана ранее.",
        task_id="task-11",
        task_status="PENDING",
    )

    with (
        patch("core.telegram.bot_handler.get_session_factory", return_value=factory),
        patch(
            "core.telegram.bot_handler.promote_recommendation_to_enable_task",
            new=AsyncMock(return_value=service_result),
        ),
    ):
        result = await _create_enable_task_from_recommendation(
            recommendation_event_id="event-11",
            tg_user_id="tg-11",
            username="owner",
        )

    assert result["outcome"] == "existing"
    assert result["created_new"] is False
    assert result["detail"] == "ℹ️ Задача на включение уже была создана ранее."
    session.commit.assert_awaited_once()

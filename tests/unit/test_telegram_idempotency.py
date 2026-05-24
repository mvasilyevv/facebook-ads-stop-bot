# -*- coding: utf-8 -*-
"""Тесты идемпотентности DisableTask по callback_token (не по текущему snapshot)."""

from __future__ import annotations

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
    return session


def _scalar_result(obj):
    """Имитирует результат session.execute().scalar_one_or_none()."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = obj
    return result


def _make_snapshot(*, open_state_token: str, fb_ad_id: str = "ad-555"):
    """Создаёт snapshot-моковый объект с минимальным набором полей."""
    return SimpleNamespace(
        id="snapshot-1",
        ad_id="ad-uuid-1",
        open_state_token=open_state_token,
        fb_ad_id=fb_ad_id,
        fb_ad=SimpleNamespace(ad_name="Test Ad", adset=None),
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
        warning_rule_codes=[],
        stop_rule_codes=[],
    )


# Idempotency_key берётся из callback_token, не из текущего snapshot.open_state_token.
@pytest.mark.asyncio
async def test_idempotency_key_uses_callback_token_not_current_snapshot():
    """Когда observer открыл новый incident (snapshot.open_state_token поменялся),
    повторный клик по той же кнопке должен использовать СТАРЫЙ callback_token
    в idempotency_key, чтобы существующая запись DisableTask нашлась и дубль
    не создался."""
    from core.telegram import bot_handler
    from core.telegram.bot_handler import _create_disable_task

    # В БД сейчас новый токен инцидента — observer уже открыл следующий incident.
    snapshot = _make_snapshot(open_state_token="incident-NEW")
    # Existing задача была создана с oригинальным callback_token (incident-OLD).
    existing_task = SimpleNamespace(id="task-1")

    session = _make_async_session(
        execute_side_effect=[
            _scalar_result(snapshot),  # поиск по fb_ad_id_hint=ad-555
        ],
        # 1) поиск active task по open_state_token=callback (нет такой), 2) поиск по idempotency_key (нашли)
        scalar_side_effect=[None, existing_task],
    )
    factory = MagicMock(return_value=session)

    with (
        patch("core.telegram.bot_handler.get_session_factory", return_value=factory),
        patch.object(
            bot_handler,
            "_build_disable_message_context_for_snapshot",
            new=AsyncMock(return_value=SimpleNamespace()),
        ),
    ):
        result = await _create_disable_task(
            snapshot_token="incident-OLD",
            tg_user_id="tg-1",
            username="owner",
            callback_token="incident-OLD",
            fb_ad_id_hint="ad-555",
        )

    assert result is not None
    assert result["created_new"] is False
    # incident_key должен остаться старым (callback_token), а не текущим в БД.
    assert result["incident_key"] == "incident-OLD"
    session.add.assert_not_called()


# Stale callback (токен не совпадает с текущим snapshot.open_state_token) отклоняется.
@pytest.mark.asyncio
async def test_stale_callback_token_rejected_before_creating_task():
    """Если callback пришёл с токеном, которого нет в БД (или snapshot уже
    в новом инциденте), handler отвечает 'Кнопка устарела' и НЕ создаёт задачу."""
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)

    with (
        patch.object(
            bot_handler,
            "resolve_telegram_access",
            new=AsyncMock(return_value=access),
        ),
        # _validate_alert_token возвращает False — токен устарел.
        patch.object(
            bot_handler,
            "_validate_alert_token",
            new=AsyncMock(return_value=False),
        ) as validate_token,
        patch.object(
            bot_handler,
            "_create_disable_task",
            new=AsyncMock(return_value=None),
        ) as create_task,
        patch.object(
            bot_handler,
            "broadcast_disable_task_queue_message",
            new=AsyncMock(),
        ) as broadcast_queue,
    ):
        # Новый формат: disable_execute:{token}:{origin_message_id}:{fb_ad_id}
        await bot_handler.handle_update(
            client,
            {
                "callback_query": {
                    "id": "cb-stale",
                    "data": "disable_execute:stale-token:41:ad-555",
                    "message": {
                        "chat": {"id": "-1003701505954", "type": "supergroup"},
                        "message_id": 41,
                        "message_thread_id": 13,
                    },
                    "from": {"id": 7, "username": "owner"},
                },
            },
        )

    validate_token.assert_awaited_once()
    create_task.assert_not_awaited()
    broadcast_queue.assert_not_awaited()
    # Юзер получил answer_callback_query с текстом про устаревание.
    calls = client.answer_callback_query.await_args_list
    # Первый вызов — ack без текста (закрытие часиков), второй — с текстом stale.
    stale_call = next(c for c in calls if "устарела" in (c.kwargs.get("text") or ""))
    assert stale_call is not None


# Аналогичная stale-проверка для disable_confirm с новым форматом.
@pytest.mark.asyncio
async def test_stale_callback_token_rejected_on_disable_confirm():
    """Stale-токен в disable_confirm не должен открыть confirm-экран."""
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)

    with (
        patch.object(
            bot_handler,
            "resolve_telegram_access",
            new=AsyncMock(return_value=access),
        ),
        patch.object(
            bot_handler,
            "_validate_alert_token",
            new=AsyncMock(return_value=False),
        ),
        patch.object(
            bot_handler,
            "_render_disable_confirm",
            new=AsyncMock(return_value=("never", {"inline_keyboard": []})),
        ) as render_confirm,
    ):
        await bot_handler.handle_update(
            client,
            {
                "callback_query": {
                    "id": "cb-stale-confirm",
                    "data": "disable_confirm:stale-token:ad-555",
                    "message": {
                        "chat": {"id": "-1003701505954", "type": "supergroup"},
                        "message_id": 41,
                        "message_thread_id": 13,
                    },
                    "from": {"id": 7, "username": "owner"},
                },
            },
        )

    render_confirm.assert_not_awaited()
    stale_msgs = [
        c
        for c in client.answer_callback_query.await_args_list
        if "устарела" in (c.kwargs.get("text") or "")
    ]
    assert stale_msgs, "Ожидалось answer_callback_query с текстом 'Кнопка устарела'"


# Свежий callback_token проходит stale-проверку и доходит до _create_disable_task.
@pytest.mark.asyncio
async def test_fresh_callback_token_passes_to_create_disable_task():
    """При совпадении callback_token с актуальным snapshot.open_state_token
    идёт штатное создание задачи, токен пробрасывается в _create_disable_task."""
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)

    task_info = {
        "fb_ad_id": "ad-555",
        "ad_name": "Ad 555",
        "created_new": True,
        "incident_key": "incident-FRESH",
        "message_context": SimpleNamespace(),
    }

    with (
        patch.object(
            bot_handler,
            "resolve_telegram_access",
            new=AsyncMock(return_value=access),
        ),
        patch.object(
            bot_handler,
            "_validate_alert_token",
            new=AsyncMock(return_value=True),
        ),
        patch.object(
            bot_handler,
            "_create_disable_task",
            new=AsyncMock(return_value=task_info),
        ) as create_task,
        patch.object(bot_handler, "_ack_disable_task_messages", new=AsyncMock()),
        patch.object(
            bot_handler,
            "broadcast_disable_task_queue_message",
            new=AsyncMock(),
        ),
    ):
        await bot_handler.handle_update(
            client,
            {
                "callback_query": {
                    "id": "cb-fresh",
                    "data": "disable_execute:incident-FRESH:41:ad-555",
                    "message": {
                        "chat": {"id": "-1003701505954", "type": "supergroup"},
                        "message_id": 41,
                        "message_thread_id": 13,
                    },
                    "from": {"id": 7, "username": "owner"},
                },
            },
        )

    create_task.assert_awaited_once()
    call_kwargs = create_task.await_args.kwargs
    assert call_kwargs["callback_token"] == "incident-FRESH"
    assert call_kwargs["fb_ad_id_hint"] == "ad-555"

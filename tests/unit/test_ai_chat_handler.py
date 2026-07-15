# -*- coding: utf-8 -*-
"""Unit-тесты AI-чата в Telegram (/ai + свободный текст в личке) — без БД, моки."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.ai_assistant.chat import ChatResponse, ToolCallTrace
from core.telegram.service import Recipient


def _tg_client() -> MagicMock:
    client = MagicMock()
    client.send_message = AsyncMock(return_value={"message_id": 10})
    client.send_chat_action = AsyncMock()
    return client


def _update(text: str, chat_type: str = "private") -> dict:
    return {
        "message": {
            "chat": {"id": 123, "type": chat_type},
            "message_id": 1,
            "from": {"id": 555, "username": "mark"},
            "text": text,
        }
    }


def _redis_mock(busy_acquired: bool = True) -> MagicMock:
    r = MagicMock()
    r.set = AsyncMock(return_value=True if busy_acquired else None)
    r.delete = AsyncMock()
    r.lrange = AsyncMock(return_value=[])
    r.rpush = AsyncMock()
    r.ltrim = AsyncMock()
    r.expire = AsyncMock()
    return r


OWNER = Recipient(123, 555, "mark", "owner")
VIEWER = Recipient(123, 555, "mark", "recipient")


# /ai от не-owner'а — отказ ⛔ ещё на роутере (ассистент умеет money-черновики)
@pytest.mark.asyncio
async def test_ai_command_denied_for_non_owner() -> None:
    from core.telegram.bot_handler import handle_update

    client = _tg_client()
    with patch("core.telegram.handlers.router.find_recipient", new=AsyncMock(return_value=VIEWER)):
        await handle_update(engine=MagicMock(), client=client, update=_update("/ai привет"))
    sent = client.send_message.call_args.kwargs["text"]
    assert "владелец" in sent.lower()


# /ai от owner'а — вопрос уходит в ChatSession (фоновым таском, H-1), ответ в чат
@pytest.mark.asyncio
async def test_ai_command_owner_gets_answer() -> None:
    import core.telegram.handlers.ai_chat as ai_mod
    from core.telegram.bot_handler import handle_update

    client = _tg_client()
    session = MagicMock()
    session.ask = AsyncMock(return_value=ChatResponse(answer="Всё спокойно"))
    with (
        patch("core.telegram.handlers.router.find_recipient", new=AsyncMock(return_value=OWNER)),
        patch("core.telegram.handlers.ai_chat.ChatSession", return_value=session) as cs,
    ):
        await handle_update(
            engine=MagicMock(),
            client=client,
            update=_update("/ai что с кабинетом?"),
            redis_client=_redis_mock(),
        )
        # H-1: чат работает фоновым таском — роутер вернулся сразу, дожидаемся таск
        import asyncio

        await asyncio.gather(*list(ai_mod._chat_tasks))
    # ChatSession создан с tools и скилом чат-оператора
    assert cs.call_args.kwargs["allow_tools"] is True
    assert "chat_operator" in cs.call_args.kwargs["skills"]
    # ask получил вопрос и owner-идентичность для draft-ACL
    ask_kwargs = session.ask.call_args.kwargs
    assert ask_kwargs["created_by_chat_id"] == 123
    assert session.ask.call_args.args[0][-1].content == "что с кабинетом?"
    texts = [c.kwargs["text"] for c in client.send_message.call_args_list]
    assert any("Всё спокойно" in t for t in texts)


# Свободный текст в личке owner'а — маршрутизируется в AI-чат без команды
@pytest.mark.asyncio
async def test_free_text_dm_owner_routes_to_ai() -> None:
    from core.telegram.bot_handler import handle_update

    with (
        patch("core.telegram.handlers.router.find_recipient", new=AsyncMock(return_value=OWNER)),
        patch("core.telegram.handlers.router.spawn_ai_chat") as ai,
    ):
        await handle_update(engine=MagicMock(), client=_tg_client(), update=_update("как дела?"))
    assert ai.call_args.kwargs["args_text"] == "как дела?"


# Свободный текст от не-owner'а (даже recipient'а) — молчаливый игнор, как раньше
@pytest.mark.asyncio
async def test_free_text_dm_non_owner_silent() -> None:
    from core.telegram.bot_handler import handle_update

    client = _tg_client()
    with patch("core.telegram.handlers.router.find_recipient", new=AsyncMock(return_value=VIEWER)):
        await handle_update(engine=MagicMock(), client=client, update=_update("привет"))
    client.send_message.assert_not_called()


# Свободный текст в группе — игнор без обращения к AI (только личка)
@pytest.mark.asyncio
async def test_free_text_group_ignored() -> None:
    from core.telegram.bot_handler import handle_update

    client = _tg_client()
    with patch("core.telegram.handlers.router.find_recipient", new=AsyncMock()) as fr:
        await handle_update(
            engine=MagicMock(), client=client, update=_update("привет", chat_type="supergroup")
        )
    fr.assert_not_awaited()
    client.send_message.assert_not_called()


# Busy-guard: пока думаем над прошлым вопросом — новый не запускаем
@pytest.mark.asyncio
async def test_busy_guard_blocks_parallel_question() -> None:
    from core.telegram.handlers.ai_chat import handle_ai_chat

    client = _tg_client()
    session = MagicMock()
    session.ask = AsyncMock()
    with patch("core.telegram.handlers.ai_chat.ChatSession", return_value=session):
        await handle_ai_chat(
            engine=MagicMock(),
            client=client,
            chat_id=123,
            message_id=1,
            thread_id=None,
            username="mark",
            args_text="вопрос",
            redis_client=_redis_mock(busy_acquired=False),
        )
    session.ask.assert_not_awaited()
    sent = client.send_message.call_args.kwargs["text"]
    assert "думаю" in sent.lower()


# Draft-инструмент в трейсе → превью черновика с кнопками dr_ok/dr_cancel
@pytest.mark.asyncio
async def test_draft_trace_sends_preview_with_buttons() -> None:
    from core.telegram.handlers.ai_chat import handle_ai_chat

    client = _tg_client()
    resp = ChatResponse(
        answer="Черновик паузы готов",
        tool_calls=[
            ToolCallTrace(
                name="request_bulk_pause",
                args={"offer_code": "GH_CR2"},
                result="DRAFT создан: task_id=77 (bulk_status_change pause, 3 объявлений)",
            )
        ],
    )
    session = MagicMock()
    session.ask = AsyncMock(return_value=resp)
    with patch("core.telegram.handlers.ai_chat.ChatSession", return_value=session):
        await handle_ai_chat(
            engine=MagicMock(),
            client=client,
            chat_id=123,
            message_id=1,
            thread_id=None,
            username="mark",
            args_text="поставь GH_CR2 на паузу",
            redis_client=_redis_mock(),
        )
    markups = [c.kwargs.get("reply_markup") for c in client.send_message.call_args_list]
    keyboards = [m for m in markups if m]
    assert keyboards, "превью черновика с клавиатурой не отправлено"
    buttons = keyboards[0]["inline_keyboard"][0]
    assert buttons[0]["callback_data"] == "dr_ok:77"
    assert buttons[1]["callback_data"] == "dr_cancel:77"


# READ_ONLY-инструмент в трейсе превью НЕ создаёт (кнопки только для черновиков)
@pytest.mark.asyncio
async def test_read_only_trace_no_preview() -> None:
    from core.telegram.handlers.ai_chat import handle_ai_chat

    client = _tg_client()
    resp = ChatResponse(
        answer="Все воркеры живы",
        tool_calls=[ToolCallTrace(name="get_worker_health", args={}, result="ONLINE: observer")],
    )
    session = MagicMock()
    session.ask = AsyncMock(return_value=resp)
    with patch("core.telegram.handlers.ai_chat.ChatSession", return_value=session):
        await handle_ai_chat(
            engine=MagicMock(),
            client=client,
            chat_id=123,
            message_id=1,
            thread_id=None,
            username="mark",
            args_text="статус",
            redis_client=_redis_mock(),
        )
    markups = [c.kwargs.get("reply_markup") for c in client.send_message.call_args_list]
    assert not any(markups)


# /ai reset — история чата в Redis удаляется, приходит подтверждение
@pytest.mark.asyncio
async def test_ai_reset_clears_history() -> None:
    from core.telegram.handlers.ai_chat import handle_ai_chat

    client = _tg_client()
    r = _redis_mock()
    await handle_ai_chat(
        engine=MagicMock(),
        client=client,
        chat_id=123,
        message_id=1,
        thread_id=None,
        username="mark",
        args_text="reset",
        redis_client=r,
    )
    r.delete.assert_awaited_with("ai:chat:hist:123")
    sent = client.send_message.call_args.kwargs["text"]
    assert "сброшен" in sent.lower()


# Кривой HTML от модели: первый sendMessage падает 400 → повтор без разметки
@pytest.mark.asyncio
async def test_invalid_html_falls_back_to_plain() -> None:
    from core.telegram.client import TelegramAPIError
    from core.telegram.handlers.ai_chat import handle_ai_chat

    client = _tg_client()
    client.send_message = AsyncMock(
        side_effect=[
            TelegramAPIError(method="sendMessage", description="bad html", error_code=400),
            {"message_id": 11},
        ]
    )
    session = MagicMock()
    session.ask = AsyncMock(return_value=ChatResponse(answer="<b>кривой<i> html"))
    with patch("core.telegram.handlers.ai_chat.ChatSession", return_value=session):
        await handle_ai_chat(
            engine=MagicMock(),
            client=client,
            chat_id=123,
            message_id=1,
            thread_id=None,
            username="mark",
            args_text="вопрос",
            redis_client=_redis_mock(),
        )
    assert client.send_message.call_count == 2
    assert client.send_message.call_args_list[1].kwargs["parse_mode"] is None


# История: обмен user/assistant дописывается в Redis c LTRIM и TTL
@pytest.mark.asyncio
async def test_history_appended_after_answer() -> None:
    from core.telegram.handlers.ai_chat import handle_ai_chat

    client = _tg_client()
    r = _redis_mock()
    session = MagicMock()
    session.ask = AsyncMock(return_value=ChatResponse(answer="ок"))
    with patch("core.telegram.handlers.ai_chat.ChatSession", return_value=session):
        await handle_ai_chat(
            engine=MagicMock(),
            client=client,
            chat_id=123,
            message_id=1,
            thread_id=None,
            username="mark",
            args_text="вопрос",
            redis_client=r,
        )
    assert r.rpush.await_count == 2  # user + assistant
    r.ltrim.assert_awaited()
    r.expire.assert_awaited()

# -*- coding: utf-8 -*-
"""Unit-тесты Telegram handler'ов creator workflow.

Проверяет: /record_plan, /stop_record, /plans и callback plan:<uuid>.
Без реальной БД и Redis — только чистая логика через моки.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.telegram.handlers.creator import (
    CHANNEL_RECORD_START,
    CHANNEL_RECORD_STOP,
    handle_list_plans,
    handle_plan_run_callback,
    handle_record_plan,
    handle_stop_record,
)

# ====================== helpers ======================


def _make_client() -> MagicMock:
    """TelegramBotClient-мок с async send_message и answer_callback_query."""
    c = MagicMock()
    c.send_message = AsyncMock(return_value={"message_id": 10})
    c.answer_callback_query = AsyncMock()
    c.edit_message_reply_markup = AsyncMock()
    return c


def _make_redis() -> MagicMock:
    """RedisPubSub-мок с async publish."""
    r = MagicMock()
    r.publish = AsyncMock()
    return r


def _make_engine() -> MagicMock:
    return MagicMock()


# ====================== /record_plan ======================


# Нормальный вызов: /record_plan <name> — публикует в правильный канал
@pytest.mark.asyncio
async def test_handle_record_plan_publishes_start() -> None:
    client = _make_client()
    redis = _make_redis()
    engine = _make_engine()

    await handle_record_plan(
        engine=engine,
        client=client,
        redis=redis,
        chat_id=111,
        message_id=1,
        thread_id=None,
        args_text="Мой тестовый план",
    )

    redis.publish.assert_awaited_once()
    channel, payload = redis.publish.call_args[0]
    assert channel == CHANNEL_RECORD_START
    assert payload["plan_name"] == "Мой тестовый план"
    assert payload["recipient_id"] == "111"
    # Ответ ушёл в TG
    client.send_message.assert_awaited_once()
    sent_text = client.send_message.call_args.kwargs["text"]
    assert "Мой тестовый план" in sent_text


# Пустое имя плана — ошибка, publish НЕ вызывается
@pytest.mark.asyncio
async def test_handle_record_plan_empty_name_error() -> None:
    client = _make_client()
    redis = _make_redis()
    engine = _make_engine()

    await handle_record_plan(
        engine=engine,
        client=client,
        redis=redis,
        chat_id=111,
        message_id=1,
        thread_id=None,
        args_text="",
    )

    redis.publish.assert_not_awaited()
    client.send_message.assert_awaited_once()
    text_ = client.send_message.call_args.kwargs["text"]
    # Сообщение содержит подсказку
    assert "record_plan" in text_


# Имя только пробелы — тоже ошибка
@pytest.mark.asyncio
async def test_handle_record_plan_whitespace_only_error() -> None:
    client = _make_client()
    redis = _make_redis()
    engine = _make_engine()

    await handle_record_plan(
        engine=engine,
        client=client,
        redis=redis,
        chat_id=222,
        message_id=2,
        thread_id=None,
        args_text="   ",
    )

    redis.publish.assert_not_awaited()


# Pipe-separated аргументы: имя и ad_account_id парсятся правильно
@pytest.mark.asyncio
async def test_handle_record_plan_pipe_args_parsed() -> None:
    client = _make_client()
    redis = _make_redis()
    engine = _make_engine()

    await handle_record_plan(
        engine=engine,
        client=client,
        redis=redis,
        chat_id=333,
        message_id=3,
        thread_id=None,
        args_text="My Plan | ad_account_id=act_12345",
    )

    redis.publish.assert_awaited_once()
    _, payload = redis.publish.call_args[0]
    assert payload["plan_name"] == "My Plan"
    assert payload["ad_account_id"] == "act_12345"


# ====================== /stop_record ======================


# Публикует в правильный канал с recipient_id
@pytest.mark.asyncio
async def test_handle_stop_record_publishes_stop() -> None:
    client = _make_client()
    redis = _make_redis()
    engine = _make_engine()

    await handle_stop_record(
        engine=engine,
        client=client,
        redis=redis,
        chat_id=777,
        message_id=5,
        thread_id=None,
    )

    redis.publish.assert_awaited_once()
    channel, payload = redis.publish.call_args[0]
    assert channel == CHANNEL_RECORD_STOP
    assert payload["recipient_id"] == "777"
    # Ответ ушёл в TG
    client.send_message.assert_awaited_once()


# ====================== /plans ======================


# Пустой список — отвечает empty-state сообщением
@pytest.mark.asyncio
async def test_handle_list_plans_empty() -> None:
    client = _make_client()
    engine = _make_engine()

    with patch(
        "core.telegram.handlers.creator._load_active_plans",
        new_callable=AsyncMock,
        return_value=[],
    ):
        await handle_list_plans(
            engine=engine,
            client=client,
            chat_id=100,
            thread_id=None,
        )

    client.send_message.assert_awaited_once()
    text_ = client.send_message.call_args.kwargs["text"]
    assert "record_plan" in text_


# Список с одним планом — кнопка «Запустить» в reply_markup
@pytest.mark.asyncio
async def test_handle_list_plans_with_plans() -> None:
    client = _make_client()
    engine = _make_engine()

    plans = [
        {
            "id": "aaaaaaaa-0000-0000-0000-000000000001",
            "name": "Test Plan",
            "created_at": "2025-01-01T10:00:00",
        }
    ]
    with patch(
        "core.telegram.handlers.creator._load_active_plans",
        new_callable=AsyncMock,
        return_value=plans,
    ):
        await handle_list_plans(
            engine=engine,
            client=client,
            chat_id=100,
            thread_id=None,
        )

    client.send_message.assert_awaited_once()
    kwargs = client.send_message.call_args.kwargs
    # Должен быть reply_markup с inline-кнопкой
    markup = kwargs.get("reply_markup") or {}
    keyboard = markup.get("inline_keyboard") or []
    assert len(keyboard) == 1
    button = keyboard[0][0]
    assert button["callback_data"] == "plan:aaaaaaaa-0000-0000-0000-000000000001"
    assert "Test Plan" in button["text"]


# ====================== callback plan:<uuid> ======================


# Callback с существующим, не архивированным планом — создаёт task_queue
@pytest.mark.asyncio
async def test_handle_plan_run_callback_creates_task() -> None:
    client = _make_client()
    engine = _make_engine()
    plan_id = "bbbbbbbb-0000-0000-0000-000000000002"

    cq = {
        "id": "cq123",
        "data": f"plan:{plan_id}",
        "from": {"id": 42, "username": "alice"},
        "message": {"message_id": 10, "chat": {"id": 100}},
    }

    with (
        patch(
            "core.telegram.handlers.creator._load_plan_for_callback",
            new_callable=AsyncMock,
            return_value={"id": plan_id, "name": "Test", "is_archived": False},
        ),
        patch(
            "core.telegram.handlers.creator.create_task",
            new_callable=AsyncMock,
            return_value=999,
        ) as mock_create,
    ):
        await handle_plan_run_callback(callback_query=cq, engine=engine, client=client)

    mock_create.assert_awaited_once()
    kwargs = mock_create.call_args.kwargs
    assert kwargs["task_type"] == "plan_run"
    assert kwargs["payload"]["plan_id"] == plan_id
    assert kwargs["requested_by"] == "user:42"
    # Idempotency ключ содержит plan_id
    assert plan_id in kwargs["idempotency_key"]
    client.answer_callback_query.assert_awaited_once()
    # Проверяем что ответ содержит номер задачи
    assert "999" in str(client.answer_callback_query.call_args)


# Callback с несуществующим plan_id — отказ, create_task не вызван
@pytest.mark.asyncio
async def test_handle_plan_run_callback_plan_not_found() -> None:
    client = _make_client()
    engine = _make_engine()

    cq = {
        "id": "cq_bad",
        "data": "plan:nonexistent-00-00-00-00-00000000",
        "from": {"id": 1, "username": "bob"},
        "message": {"message_id": 1, "chat": {"id": 50}},
    }

    with (
        patch(
            "core.telegram.handlers.creator._load_plan_for_callback",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "core.telegram.handlers.creator.create_task",
            new_callable=AsyncMock,
        ) as mock_create,
    ):
        await handle_plan_run_callback(callback_query=cq, engine=engine, client=client)

    mock_create.assert_not_awaited()
    client.answer_callback_query.assert_awaited_once()
    call_str = str(client.answer_callback_query.call_args)
    assert "не найден" in call_str or "не найд" in call_str


# Callback с архивированным планом — отказ
@pytest.mark.asyncio
async def test_handle_plan_run_callback_archived_plan() -> None:
    client = _make_client()
    engine = _make_engine()
    plan_id = "cccccccc-0000-0000-0000-000000000003"

    cq = {
        "id": "cq_arch",
        "data": f"plan:{plan_id}",
        "from": {"id": 1, "username": "carol"},
        "message": {"message_id": 1, "chat": {"id": 50}},
    }

    with (
        patch(
            "core.telegram.handlers.creator._load_plan_for_callback",
            new_callable=AsyncMock,
            return_value={"id": plan_id, "name": "Old", "is_archived": True},
        ),
        patch(
            "core.telegram.handlers.creator.create_task",
            new_callable=AsyncMock,
        ) as mock_create,
    ):
        await handle_plan_run_callback(callback_query=cq, engine=engine, client=client)

    mock_create.assert_not_awaited()
    client.answer_callback_query.assert_awaited_once()
    call_str = str(client.answer_callback_query.call_args)
    assert "архивирован" in call_str


# Если create_task вернул None (idempotency hit в 60-секундном окне) — ответ «уже в очереди»
@pytest.mark.asyncio
async def test_handle_plan_run_callback_idempotency_already_queued() -> None:
    client = _make_client()
    engine = _make_engine()
    plan_id = "dddddddd-0000-0000-0000-000000000004"

    cq = {
        "id": "cq_idem",
        "data": f"plan:{plan_id}",
        "from": {"id": 5, "username": "dave"},
        "message": {"message_id": 2, "chat": {"id": 60}},
    }

    with (
        patch(
            "core.telegram.handlers.creator._load_plan_for_callback",
            new_callable=AsyncMock,
            return_value={"id": plan_id, "name": "Plan", "is_archived": False},
        ),
        patch(
            "core.telegram.handlers.creator.create_task",
            new_callable=AsyncMock,
            return_value=None,  # уже существует
        ),
    ):
        await handle_plan_run_callback(callback_query=cq, engine=engine, client=client)

    client.answer_callback_query.assert_awaited_once()
    call_str = str(client.answer_callback_query.call_args)
    assert "очередь" in call_str or "окно" in call_str

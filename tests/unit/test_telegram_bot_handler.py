# -*- coding: utf-8 -*-
"""Unit-тесты для telegram_v2 bot_handler — без БД, через моки."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Сценарий: не /-команда — handler должен молча выйти
@pytest.mark.asyncio
async def test_non_command_ignored() -> None:
    from core.telegram.bot_handler import handle_update

    engine = MagicMock()
    client = MagicMock()
    client.send_message = AsyncMock()

    update = {
        "message": {
            "chat": {"id": 123, "type": "private"},
            "message_id": 1,
            "from": {"id": 555, "username": "alice"},
            "text": "просто привет",
        }
    }
    await handle_update(engine=engine, client=client, update=update)
    client.send_message.assert_not_called()


# Сценарий: /start без кода в личке → подсказка с просьбой ввести код
@pytest.mark.asyncio
async def test_start_without_code_private() -> None:
    from core.telegram.bot_handler import handle_update

    engine = MagicMock()
    client = MagicMock()
    client.send_message = AsyncMock()

    update = {
        "message": {
            "chat": {"id": 123, "type": "private"},
            "message_id": 1,
            "from": {"id": 555, "username": "alice"},
            "text": "/start",
        }
    }
    await handle_update(engine=engine, client=client, update=update)
    client.send_message.assert_awaited_once()
    sent = client.send_message.call_args.kwargs["text"]
    assert "код-приглашение" in sent.lower() or "пришли" in sent.lower()


# Сценарий: незнакомая команда → ответ "неизвестная команда"
@pytest.mark.asyncio
async def test_unknown_command_for_recipient() -> None:
    from core.telegram.bot_handler import handle_update
    from core.telegram.service import Recipient

    engine = MagicMock()
    client = MagicMock()
    client.send_message = AsyncMock()

    with patch(
        "core.telegram.handlers.router.find_recipient",
        new=AsyncMock(return_value=Recipient(123, 555, "alice", "recipient")),
    ):
        update = {
            "message": {
                "chat": {"id": 123, "type": "private"},
                "message_id": 1,
                "from": {"id": 555, "username": "alice"},
                "text": "/unknowncmd",
            }
        }
        await handle_update(engine=engine, client=client, update=update)

    client.send_message.assert_awaited_once()
    sent = client.send_message.call_args.kwargs["text"]
    assert "неизвестная" in sent.lower() or "unknown" in sent.lower()


# Сценарий: legacy команды отвечают заглушкой "в процессе миграции"
@pytest.mark.asyncio
async def test_legacy_command_stub() -> None:
    from core.telegram.bot_handler import handle_update
    from core.telegram.service import Recipient

    engine = MagicMock()
    client = MagicMock()
    client.send_message = AsyncMock()

    with patch(
        "core.telegram.handlers.router.find_recipient",
        new=AsyncMock(return_value=Recipient(123, 555, "alice", "owner")),
    ):
        update = {
            "message": {
                "chat": {"id": 123, "type": "private"},
                "message_id": 1,
                "from": {"id": 555, "username": "alice"},
                "text": "/ads",
            }
        }
        await handle_update(engine=engine, client=client, update=update)

    sent = client.send_message.call_args.kwargs["text"]
    assert "миграции" in sent.lower()


# Сценарий: /help для recipient'a → список команд
@pytest.mark.asyncio
async def test_help_for_recipient() -> None:
    from core.telegram.bot_handler import handle_update
    from core.telegram.service import Recipient

    engine = MagicMock()
    client = MagicMock()
    client.send_message = AsyncMock()

    with patch(
        "core.telegram.handlers.router.find_recipient",
        new=AsyncMock(return_value=Recipient(123, 555, "alice", "recipient")),
    ):
        update = {
            "message": {
                "chat": {"id": 123, "type": "private"},
                "message_id": 1,
                "from": {"id": 555, "username": "alice"},
                "text": "/help",
            }
        }
        await handle_update(engine=engine, client=client, update=update)

    sent = client.send_message.call_args.kwargs["text"]
    assert "/spy" in sent
    assert "/help" in sent


# Сценарий: /spy без аргументов → ошибка парсинга
@pytest.mark.asyncio
async def test_spy_missing_args() -> None:
    from core.telegram.bot_handler import handle_update
    from core.telegram.service import Recipient

    engine = MagicMock()
    client = MagicMock()
    client.send_message = AsyncMock()

    with patch(
        "core.telegram.handlers.router.find_recipient",
        new=AsyncMock(return_value=Recipient(123, 555, "alice", "recipient")),
    ):
        update = {
            "message": {
                "chat": {"id": 123, "type": "private"},
                "message_id": 1,
                "from": {"id": 555, "username": "alice"},
                "text": "/spy",
            }
        }
        await handle_update(engine=engine, client=client, update=update)

    # Ожидаем что был ровно один вызов send_message с подсказкой
    client.send_message.assert_awaited_once()
    sent = client.send_message.call_args.kwargs["text"]
    assert "/spy" in sent
    assert "country" in sent.lower() or "слот" in sent.lower()


# Сценарий: /spy с правильными аргументами → "Сканирую..." + background pipeline task
@pytest.mark.asyncio
async def test_spy_kicks_off_pipeline() -> None:
    from core.telegram.bot_handler import handle_update
    from core.telegram.service import Recipient

    engine = MagicMock()
    client = MagicMock()
    client.send_message = AsyncMock(return_value={"message_id": 42})

    # Мокаем pipeline чтобы он не запускал реальный gRPC
    fake_pipeline_result = MagicMock()
    fake_pipeline_result.report = {"markdown_report": "# fake report"}

    with (
        patch(
            "core.telegram.handlers.router.find_recipient",
            new=AsyncMock(return_value=Recipient(123, 555, "alice", "recipient")),
        ),
        patch(
            "core.telegram.handlers.spy.run_pipeline",
            new=AsyncMock(return_value=fake_pipeline_result),
        ),
        patch(
            "core.telegram.handlers.spy.format_short_summary",
            new=lambda res: "SUMMARY OK",
        ),
    ):
        update = {
            "message": {
                "chat": {"id": 123, "type": "private"},
                "message_id": 1,
                "from": {"id": 555, "username": "alice"},
                "text": "/spy chicken road 2 KE",
            }
        }
        await handle_update(engine=engine, client=client, update=update)

        # Первый send_message — "Сканирую…"
        first_call = client.send_message.call_args_list[0]
        assert "Сканирую" in first_call.kwargs["text"]

        # Background pipeline task: дождаться ПОКА patch ещё активен —
        # иначе run_pipeline размокается и пойдёт в реальный gRPC.
        import asyncio

        pending = {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}
        if pending:
            await asyncio.wait(pending, timeout=2.0)

        # После завершения task должен быть отправлен summary
        sent_texts = [c.kwargs["text"] for c in client.send_message.call_args_list]
        assert any("SUMMARY OK" in t for t in sent_texts), f"summary not found in {sent_texts}"


# Сценарий: команда с @suffix (/spy@my_bot) — должна работать как /spy
@pytest.mark.asyncio
async def test_command_with_bot_username_suffix() -> None:
    from core.telegram.bot_handler import handle_update
    from core.telegram.service import Recipient

    engine = MagicMock()
    client = MagicMock()
    client.send_message = AsyncMock()

    with patch(
        "core.telegram.handlers.router.find_recipient",
        new=AsyncMock(return_value=Recipient(123, 555, "alice", "recipient")),
    ):
        update = {
            "message": {
                "chat": {"id": 123, "type": "group"},
                "message_id": 1,
                "from": {"id": 555, "username": "alice"},
                "text": "/help@my_test_bot",
            }
        }
        await handle_update(engine=engine, client=client, update=update)

    sent = client.send_message.call_args.kwargs["text"]
    assert "/spy" in sent  # /help сработал


# Сценарий: callback_query c некорректным data → answer_callback_query без crash
@pytest.mark.asyncio
async def test_callback_query_invalid_data() -> None:
    from core.telegram.bot_handler import handle_update

    engine = MagicMock()
    client = MagicMock()
    client.send_message = AsyncMock()
    client.answer_callback_query = AsyncMock()

    update = {
        "callback_query": {
            "id": "1",
            "data": "noop",  # один токен — не parse'нется
            "from": {"id": 555, "username": "alice"},
            "message": {"chat": {"id": 123}, "message_id": 1},
        }
    }
    await handle_update(engine=engine, client=client, update=update)
    # send_message не вызывается (это inline-кнопка, не текст)
    client.send_message.assert_not_called()
    # answer_callback_query вызван с ошибкой формата
    client.answer_callback_query.assert_awaited_once()
    args = client.answer_callback_query.call_args
    assert "формат" in args.kwargs.get("text", "").lower() or "формат" in (
        args.args[1] if len(args.args) > 1 else ""
    )

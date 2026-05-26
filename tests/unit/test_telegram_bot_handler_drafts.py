# -*- coding: utf-8 -*-
"""Тесты Telegram-команд /clone, /budget, /pause_offer и draft-callbacks.

Покрывают wave 3.3: новые TG-команды и кнопки Confirm/Reject для DRAFT-задач.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.domain import TelegramUserRole


def _make_supergroup_message(text: str, *, user_id: int = 1, username: str = "owner") -> dict:
    """Заготовка update для команды в supergroup."""
    return {
        "message": {
            "chat": {"id": "-1003701505954", "type": "supergroup"},
            "message_thread_id": 11,
            "from": {"id": user_id, "username": username},
            "text": text,
        },
    }


def _make_callback_update(data: str, *, user_id: int = 1, username: str = "owner") -> dict:
    """Заготовка update для callback_query."""
    return {
        "callback_query": {
            "id": "cb1",
            "data": data,
            "from": {"id": user_id, "username": username},
            "message": {
                "chat": {"id": "-1003701505954", "type": "supergroup"},
                "message_id": 555,
                "message_thread_id": 11,
                "text": "🗒 Черновик задачи\n\ntask_id: " + str(uuid.uuid4()),
            },
        },
    }


# ─── /clone ───────────────────────────────────────────────────────────────


# Сценарий: owner вызывает /clone — tool создаёт DRAFT, бот отправляет сообщение с кнопками.
@pytest.mark.asyncio
async def test_clone_command_owner_creates_draft_with_buttons():
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)
    task_id = uuid.uuid4()
    tool_output = (
        f"Черновик создан.\n"
        f"task_id: {task_id}\n"
        f"mutation_kind: clone_campaign\n"
        f"Кампания: 120201234567890 (кабинет act_123) → Meta добавит -Copy\n"
        f"Глубина: полный клон (adsets + ads)\n"
        f"Причина: тестовая команда\n"
        f"Подтвердите в Telegram чтобы исполнить."
    )

    with (
        patch.object(bot_handler, "resolve_telegram_access", new=AsyncMock(return_value=access)),
        patch(
            "core.ai_assistant.tools.drafts.request_clone_campaign.RequestCloneCampaignTool.run",
            new=AsyncMock(return_value=tool_output),
        ),
    ):
        await bot_handler.handle_update(
            client,
            _make_supergroup_message("/clone act_123 120201234567890 проверка"),
        )

    client.send_message.assert_awaited_once()
    sent_text = client.send_message.await_args.kwargs["text"]
    reply_markup = client.send_message.await_args.kwargs.get("reply_markup")
    assert "Клонирование кампании" in sent_text
    assert reply_markup is not None
    buttons = reply_markup["inline_keyboard"][0]
    assert any("draft_confirm" in b["callback_data"] for b in buttons)
    assert any("draft_reject" in b["callback_data"] for b in buttons)


# Сценарий: recipient получает OWNER_ONLY на /clone.
@pytest.mark.asyncio
async def test_clone_command_recipient_403():
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.RECIPIENT.value)

    with patch.object(bot_handler, "resolve_telegram_access", new=AsyncMock(return_value=access)):
        await bot_handler.handle_update(
            client,
            _make_supergroup_message("/clone act_123 120201234567890", user_id=7, username="guest"),
        )

    client.send_message.assert_awaited_once()
    assert bot_handler.OWNER_ONLY_TEXT in client.send_message.await_args.kwargs["text"]


# Сценарий: /clone без аргументов → подсказка использования.
@pytest.mark.asyncio
async def test_clone_command_without_args_shows_usage():
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)

    with patch.object(bot_handler, "resolve_telegram_access", new=AsyncMock(return_value=access)):
        await bot_handler.handle_update(client, _make_supergroup_message("/clone"))

    client.send_message.assert_awaited_once()
    text = client.send_message.await_args.kwargs["text"]
    assert "Использование" in text


# ─── /budget ──────────────────────────────────────────────────────────────


# Сценарий: owner /budget act_X adset_id 50 → tool вызывается с daily_budget_usd=50.
@pytest.mark.asyncio
async def test_budget_command_owner_creates_draft():
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)
    task_id = uuid.uuid4()
    tool_output = (
        f"Черновик создан.\n"
        f"task_id: {task_id}\n"
        f"mutation_kind: set_budget\n"
        f"Объект: adset 23842 (кабинет act_123)\n"
        f"Изменение: дневной бюджет 50.00 USD (5000 центов)\n"
        f"Причина: высокий CPL\n"
        f"Подтвердите в Telegram чтобы исполнить."
    )

    tool_run = AsyncMock(return_value=tool_output)
    with (
        patch.object(bot_handler, "resolve_telegram_access", new=AsyncMock(return_value=access)),
        patch(
            "core.ai_assistant.tools.drafts.request_budget_change.RequestBudgetChangeTool.run",
            new=tool_run,
        ),
    ):
        await bot_handler.handle_update(
            client,
            _make_supergroup_message("/budget act_123 23842 50 высокий CPL"),
        )

    tool_run.assert_awaited_once()
    args = tool_run.await_args.args[0]
    assert args["ad_account_id"] == "act_123"
    assert args["entity_id"] == "23842"
    assert args["daily_budget_usd"] == 50.0
    assert args["entity_type"] == "adset"
    client.send_message.assert_awaited_once()
    sent_text = client.send_message.await_args.kwargs["text"]
    assert "Изменение бюджета" in sent_text


# Сценарий: /budget с нечисловой суммой → понятная ошибка валидации.
@pytest.mark.asyncio
async def test_budget_command_invalid_amount_returns_validation_error():
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)

    with patch.object(bot_handler, "resolve_telegram_access", new=AsyncMock(return_value=access)):
        await bot_handler.handle_update(
            client,
            _make_supergroup_message("/budget act_123 23842 abc"),
        )

    client.send_message.assert_awaited_once()
    text = client.send_message.await_args.kwargs["text"]
    assert "числом" in text


# Сценарий: /budget с amount <= 0 → отказ.
@pytest.mark.asyncio
async def test_budget_command_negative_amount_rejected():
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)

    with patch.object(bot_handler, "resolve_telegram_access", new=AsyncMock(return_value=access)):
        await bot_handler.handle_update(
            client,
            _make_supergroup_message("/budget act_123 23842 -10"),
        )

    client.send_message.assert_awaited_once()
    text = client.send_message.await_args.kwargs["text"]
    assert "положительным" in text


# ─── /pause_offer ─────────────────────────────────────────────────────────


# Сценарий: /pause_offer act_X DRC_CR2 30 → tool вызывается с filter={offer_code, cpl_gt=30}.
@pytest.mark.asyncio
async def test_pause_offer_command_with_cpl_max():
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)
    task_id = uuid.uuid4()
    tool_output = (
        f"Черновик создан.\n"
        f"task_id: {task_id}\n"
        f"mutation_kind: bulk_pause\n"
        f"Кабинет: act_123\n"
        f"Объявлений к паузе: 5\n"
        f"  - ad_001\n"
        f"Причина: CPL слишком высокий\n"
        f"Подтвердите в Telegram чтобы исполнить."
    )

    tool_run = AsyncMock(return_value=tool_output)
    with (
        patch.object(bot_handler, "resolve_telegram_access", new=AsyncMock(return_value=access)),
        patch(
            "core.ai_assistant.tools.drafts.request_bulk_pause.RequestBulkPauseTool.run",
            new=tool_run,
        ),
    ):
        await bot_handler.handle_update(
            client,
            _make_supergroup_message("/pause_offer act_123 DRC_CR2 30 CPL слишком высокий"),
        )

    tool_run.assert_awaited_once()
    args = tool_run.await_args.args[0]
    assert args["ad_account_id"] == "act_123"
    assert args["filter"]["offer_code"] == "DRC_CR2"
    assert args["filter"]["cpl_gt"] == 30.0


# Сценарий: /pause_offer act_X DRC_CR2 без cpl — фильтр только по offer_code.
@pytest.mark.asyncio
async def test_pause_offer_command_without_cpl_max():
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)
    task_id = uuid.uuid4()

    tool_run = AsyncMock(return_value=f"Черновик создан.\ntask_id: {task_id}\nПодтвердите.")
    with (
        patch.object(bot_handler, "resolve_telegram_access", new=AsyncMock(return_value=access)),
        patch(
            "core.ai_assistant.tools.drafts.request_bulk_pause.RequestBulkPauseTool.run",
            new=tool_run,
        ),
    ):
        await bot_handler.handle_update(
            client,
            _make_supergroup_message("/pause_offer act_123 DRC_CR2"),
        )

    tool_run.assert_awaited_once()
    args = tool_run.await_args.args[0]
    assert args["filter"]["offer_code"] == "DRC_CR2"
    assert "cpl_gt" not in args["filter"]


# ─── Callback draft_confirm / draft_reject ────────────────────────────────


# Сценарий: owner нажимает draft_confirm:{uuid} → approve_draft_task вызван, сообщение отредактировано.
@pytest.mark.asyncio
async def test_draft_confirm_callback_approves_task():
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)
    task_id = uuid.uuid4()

    with (
        patch.object(bot_handler, "resolve_telegram_access", new=AsyncMock(return_value=access)),
        patch.object(
            bot_handler,
            "_approve_draft_task_from_tg",
            new=AsyncMock(),
        ) as approve_mock,
        patch.object(bot_handler, "_safe_edit_current_message", new=AsyncMock()) as edit_mock,
    ):
        await bot_handler.handle_update(client, _make_callback_update(f"draft_confirm:{task_id}"))

    approve_mock.assert_awaited_once()
    edit_mock.assert_awaited_once()
    edited_text = edit_mock.await_args.kwargs["text"]
    assert "Подтверждено" in edited_text


# Сценарий: owner нажимает draft_reject:{uuid} → cancel_draft_task вызван, сообщение помечено как отменённое.
@pytest.mark.asyncio
async def test_draft_reject_callback_cancels_task():
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)
    task_id = uuid.uuid4()

    with (
        patch.object(bot_handler, "resolve_telegram_access", new=AsyncMock(return_value=access)),
        patch.object(
            bot_handler,
            "_cancel_draft_task_from_tg",
            new=AsyncMock(),
        ) as cancel_mock,
        patch.object(bot_handler, "_safe_edit_current_message", new=AsyncMock()) as edit_mock,
    ):
        await bot_handler.handle_update(client, _make_callback_update(f"draft_reject:{task_id}"))

    cancel_mock.assert_awaited_once()
    edit_mock.assert_awaited_once()
    edited_text = edit_mock.await_args.kwargs["text"]
    assert "Отменено" in edited_text


# Сценарий: recipient нажимает draft_confirm — получает OWNER_ONLY_TEXT.
@pytest.mark.asyncio
async def test_draft_confirm_callback_recipient_403():
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.RECIPIENT.value)
    task_id = uuid.uuid4()

    with (
        patch.object(bot_handler, "resolve_telegram_access", new=AsyncMock(return_value=access)),
        patch.object(bot_handler, "_approve_draft_task_from_tg", new=AsyncMock()) as approve_mock,
    ):
        await bot_handler.handle_update(
            client,
            _make_callback_update(f"draft_confirm:{task_id}", user_id=7, username="guest"),
        )

    approve_mock.assert_not_awaited()
    client.send_message.assert_awaited_once()
    assert bot_handler.OWNER_ONLY_TEXT in client.send_message.await_args.kwargs["text"]


# Сценарий: callback с некорректным UUID → сообщение об ошибке, approve не вызывается.
@pytest.mark.asyncio
async def test_draft_confirm_callback_bad_uuid_returns_error():
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)

    with (
        patch.object(bot_handler, "resolve_telegram_access", new=AsyncMock(return_value=access)),
        patch.object(bot_handler, "_approve_draft_task_from_tg", new=AsyncMock()) as approve_mock,
        patch.object(bot_handler, "_safe_edit_current_message", new=AsyncMock()) as edit_mock,
    ):
        await bot_handler.handle_update(
            client,
            _make_callback_update("draft_confirm:not-a-uuid"),
        )

    approve_mock.assert_not_awaited()
    edit_mock.assert_awaited_once()
    assert "Некорректный task_id" in edit_mock.await_args.kwargs["text"]


# Сценарий: approve_draft_task бросает ValueError (задача не в DRAFT) → сообщение об ошибке.
@pytest.mark.asyncio
async def test_draft_confirm_callback_stale_task_shows_error():
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)
    task_id = uuid.uuid4()

    with (
        patch.object(bot_handler, "resolve_telegram_access", new=AsyncMock(return_value=access)),
        patch.object(
            bot_handler,
            "_approve_draft_task_from_tg",
            new=AsyncMock(side_effect=ValueError("Задача не в статусе DRAFT")),
        ),
        patch.object(bot_handler, "_safe_edit_current_message", new=AsyncMock()) as edit_mock,
    ):
        await bot_handler.handle_update(client, _make_callback_update(f"draft_confirm:{task_id}"))

    edit_mock.assert_awaited_once()
    assert "DRAFT" in edit_mock.await_args.kwargs["text"]

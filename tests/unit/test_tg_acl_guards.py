# -*- coding: utf-8 -*-
"""Unit-тесты ACL-гейтов в Telegram-роутере: money-действия только для role='owner'.

Owner-only: callback dis/ereco/plan, команды pause/resume/record_plan/stop_record,
autostart-write. Не-money (snooze, autostart-read) — любому активному recipient.
Гейт централизован в router (без изменения сигнатур хендлеров) — мокаем
find_recipient (роль) и сами хендлеры (spy).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import core.telegram.handlers.router as router
from core.telegram.service import Recipient


def _owner() -> Recipient:
    return Recipient(chat_id=1, telegram_user_id=2, username="u", role="owner")


def _viewer() -> Recipient:
    return Recipient(chat_id=1, telegram_user_id=2, username="u", role="recipient")


def _cq(data: str) -> dict:
    return {
        "id": "cq1",
        "data": data,
        "from": {"id": 2, "username": "u"},
        "message": {"chat": {"id": 1}, "message_id": 9},
    }


def _cmd_update(text: str) -> dict:
    return {
        "message": {
            "chat": {"id": 1, "type": "private"},
            "message_id": 5,
            "from": {"id": 2, "username": "u"},
            "text": text,
        }
    }


# ====================== callbacks ======================


# dis: от владельца → хендлер вызывается
@pytest.mark.asyncio
async def test_dis_owner_allowed(monkeypatch) -> None:
    monkeypatch.setattr(router, "find_recipient", AsyncMock(return_value=_owner()))
    spy = AsyncMock()
    monkeypatch.setattr(router, "handle_dis_callback", spy)
    client = AsyncMock()
    await router._dispatch_callback_query(engine=object(), client=client, cq=_cq("dis:123:tok"))
    spy.assert_awaited_once()


# dis: от обычного recipient → отказ, хендлер НЕ вызывается
@pytest.mark.asyncio
async def test_dis_viewer_denied(monkeypatch) -> None:
    monkeypatch.setattr(router, "find_recipient", AsyncMock(return_value=_viewer()))
    spy = AsyncMock()
    monkeypatch.setattr(router, "handle_dis_callback", spy)
    client = AsyncMock()
    await router._dispatch_callback_query(engine=object(), client=client, cq=_cq("dis:123:tok"))
    spy.assert_not_awaited()
    client.answer_callback_query.assert_awaited()


# ereco: от recipient → отказ
@pytest.mark.asyncio
async def test_ereco_viewer_denied(monkeypatch) -> None:
    monkeypatch.setattr(router, "find_recipient", AsyncMock(return_value=_viewer()))
    spy = AsyncMock()
    monkeypatch.setattr(router, "handle_enable_reco_callback", spy)
    client = AsyncMock()
    await router._dispatch_callback_query(engine=object(), client=client, cq=_cq("ereco:123"))
    spy.assert_not_awaited()


# plan: от recipient → отказ
@pytest.mark.asyncio
async def test_plan_viewer_denied(monkeypatch) -> None:
    monkeypatch.setattr(router, "find_recipient", AsyncMock(return_value=_viewer()))
    spy = AsyncMock()
    monkeypatch.setattr(router, "handle_plan_run_callback", spy)
    client = AsyncMock()
    await router._dispatch_callback_query(engine=object(), client=client, cq=_cq("plan:abc"))
    spy.assert_not_awaited()


# snz: (snooze) УБРАН — больше не роутится ни в какой хендлер (no-op, не падает)
@pytest.mark.asyncio
async def test_snz_removed_no_op(monkeypatch) -> None:
    monkeypatch.setattr(router, "find_recipient", AsyncMock(return_value=_viewer()))
    dis_spy = AsyncMock()
    monkeypatch.setattr(router, "handle_dis_callback", dis_spy)
    client = AsyncMock()
    # snz больше не обрабатывается: не бросает и не дёргает dis-хендлер
    await router._dispatch_callback_query(engine=object(), client=client, cq=_cq("snz:123"))
    dis_spy.assert_not_awaited()


# dr_ok: подтверждение money-черновика (/pause) от владельца → хендлер вызывается (H-2)
@pytest.mark.asyncio
async def test_dr_ok_owner_allowed(monkeypatch) -> None:
    monkeypatch.setattr(router, "find_recipient", AsyncMock(return_value=_owner()))
    spy = AsyncMock()
    monkeypatch.setattr(router, "handle_draft_callback", spy)
    client = AsyncMock()
    await router._dispatch_callback_query(engine=object(), client=client, cq=_cq("dr_ok:55"))
    spy.assert_awaited_once()


# dr_ok: от recipient → ОТКАЗ (money-исполнение только owner). H-2 — ключевой кейс.
@pytest.mark.asyncio
async def test_dr_ok_viewer_denied(monkeypatch) -> None:
    monkeypatch.setattr(router, "find_recipient", AsyncMock(return_value=_viewer()))
    spy = AsyncMock()
    monkeypatch.setattr(router, "handle_draft_callback", spy)
    client = AsyncMock()
    await router._dispatch_callback_query(engine=object(), client=client, cq=_cq("dr_ok:55"))
    spy.assert_not_awaited()
    client.answer_callback_query.assert_awaited()


# dr_cancel: отмена черновика от recipient → РАЗРЕШЕНО (не money, снимает действие)
@pytest.mark.asyncio
async def test_dr_cancel_viewer_allowed(monkeypatch) -> None:
    monkeypatch.setattr(router, "find_recipient", AsyncMock(return_value=_viewer()))
    spy = AsyncMock()
    monkeypatch.setattr(router, "handle_draft_callback", spy)
    client = AsyncMock()
    await router._dispatch_callback_query(engine=object(), client=client, cq=_cq("dr_cancel:55"))
    spy.assert_awaited_once()


# ====================== команды ======================


# /pause от владельца → хендлер вызывается
@pytest.mark.asyncio
async def test_pause_owner_allowed(monkeypatch) -> None:
    monkeypatch.setattr(router, "find_recipient", AsyncMock(return_value=_owner()))
    spy = AsyncMock()
    monkeypatch.setattr(router, "handle_bulk_toggle", spy)
    client = AsyncMock()
    await router.handle_update(engine=object(), client=client, update=_cmd_update("/pause CR2"))
    spy.assert_awaited_once()


# /pause от recipient → отказ, хендлер НЕ вызывается
@pytest.mark.asyncio
async def test_pause_viewer_denied(monkeypatch) -> None:
    monkeypatch.setattr(router, "find_recipient", AsyncMock(return_value=_viewer()))
    spy = AsyncMock()
    monkeypatch.setattr(router, "handle_bulk_toggle", spy)
    monkeypatch.setattr(router, "send_text", AsyncMock())
    client = AsyncMock()
    await router.handle_update(engine=object(), client=client, update=_cmd_update("/pause CR2"))
    spy.assert_not_awaited()
    router.send_text.assert_awaited()


# /autostart без аргументов (чтение) от recipient → РАЗРЕШЕНО
@pytest.mark.asyncio
async def test_autostart_read_viewer_allowed(monkeypatch) -> None:
    monkeypatch.setattr(router, "find_recipient", AsyncMock(return_value=_viewer()))
    spy = AsyncMock()
    monkeypatch.setattr(router, "handle_autostart", spy)
    client = AsyncMock()
    await router.handle_update(engine=object(), client=client, update=_cmd_update("/autostart"))
    spy.assert_awaited_once()


# /autostart on (запись) от recipient → отказ
@pytest.mark.asyncio
async def test_autostart_write_viewer_denied(monkeypatch) -> None:
    monkeypatch.setattr(router, "find_recipient", AsyncMock(return_value=_viewer()))
    spy = AsyncMock()
    monkeypatch.setattr(router, "handle_autostart", spy)
    monkeypatch.setattr(router, "send_text", AsyncMock())
    client = AsyncMock()
    await router.handle_update(engine=object(), client=client, update=_cmd_update("/autostart on"))
    spy.assert_not_awaited()


# /setup_topics от владельца → хендлер вызывается (создание топиков — owner-only)
@pytest.mark.asyncio
async def test_setup_topics_owner_allowed(monkeypatch) -> None:
    monkeypatch.setattr(router, "find_recipient", AsyncMock(return_value=_owner()))
    spy = AsyncMock()
    monkeypatch.setattr(router, "handle_setup_topics", spy)
    client = AsyncMock()
    await router.handle_update(engine=object(), client=client, update=_cmd_update("/setup_topics"))
    spy.assert_awaited_once()


# /setup_topics от recipient → отказ, хендлер НЕ вызывается
@pytest.mark.asyncio
async def test_setup_topics_viewer_denied(monkeypatch) -> None:
    monkeypatch.setattr(router, "find_recipient", AsyncMock(return_value=_viewer()))
    spy = AsyncMock()
    monkeypatch.setattr(router, "handle_setup_topics", spy)
    monkeypatch.setattr(router, "send_text", AsyncMock())
    client = AsyncMock()
    await router.handle_update(engine=object(), client=client, update=_cmd_update("/setup_topics"))
    spy.assert_not_awaited()
    router.send_text.assert_awaited()


# /topics (чтение раскладки) от recipient → РАЗРЕШЕНО (не money, read-only)
@pytest.mark.asyncio
async def test_topics_read_viewer_allowed(monkeypatch) -> None:
    monkeypatch.setattr(router, "find_recipient", AsyncMock(return_value=_viewer()))
    spy = AsyncMock()
    monkeypatch.setattr(router, "handle_topics", spy)
    client = AsyncMock()
    await router.handle_update(engine=object(), client=client, update=_cmd_update("/topics"))
    spy.assert_awaited_once()


# Recipient.is_owner() предикат
def test_recipient_is_owner_predicate() -> None:
    assert _owner().is_owner() is True
    assert _viewer().is_owner() is False

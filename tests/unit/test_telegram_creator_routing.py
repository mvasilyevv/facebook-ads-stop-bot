# -*- coding: utf-8 -*-
"""Маршрутизация creator-команд через router + контракт имён каналов.

Дополняет test_telegram_handlers_creator.py (там handlers тестируются напрямую):
здесь проверяется путь через handle_update и анти-регресс рассинхрона каналов
между TG-стороной (core/telegram/handlers/creator.py) и consumer'ом
(apps/creator_recorder/main.py). Запись плана навигирует боевой браузер, поэтому
отказ при отсутствии Redis-транспорта должен быть явным, а не молчаливым.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.telegram.handlers import router as router_mod
from core.telegram.service import Recipient


def _make_client() -> MagicMock:
    c = MagicMock()
    c.send_message = AsyncMock(return_value={"message_id": 1})
    return c


def _update(text: str) -> dict[str, Any]:
    return {
        "message": {
            "chat": {"id": 555, "type": "private"},
            "from": {"id": 999, "username": "mark"},
            "message_id": 7,
            "text": text,
        }
    }


# router маршрутизирует /record_plan и пробрасывает redis + args_text в handler
@pytest.mark.asyncio
async def test_router_passes_redis_and_args_to_record_plan(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def _fake_record_plan(**kwargs: Any) -> None:
        captured.update(kwargs)

    async def _fake_find_recipient(_engine: Any, **_kw: Any) -> Recipient:
        return Recipient(chat_id=555, telegram_user_id=999, username="mark", role="owner")

    monkeypatch.setattr(router_mod, "handle_record_plan", _fake_record_plan)
    monkeypatch.setattr(router_mod, "find_recipient", _fake_find_recipient)

    redis = MagicMock()
    await router_mod.handle_update(
        engine=AsyncMock(),
        client=_make_client(),
        update=_update("/record_plan Моя кампания"),
        redis=redis,
    )

    assert captured.get("args_text") == "Моя кампания"
    assert captured.get("redis") is redis


# redis=None (poller без pubsub) → юзеру явный отказ, handle_record_plan НЕ вызывается
@pytest.mark.asyncio
async def test_router_record_plan_without_redis_warns(monkeypatch) -> None:
    called = {"n": 0}

    async def _fake_record_plan(**_kw: Any) -> None:
        called["n"] += 1

    async def _fake_find_recipient(_engine: Any, **_kw: Any) -> Recipient:
        return Recipient(chat_id=555, telegram_user_id=999, username="mark", role="owner")

    monkeypatch.setattr(router_mod, "handle_record_plan", _fake_record_plan)
    monkeypatch.setattr(router_mod, "find_recipient", _fake_find_recipient)

    client = _make_client()
    await router_mod.handle_update(
        engine=AsyncMock(),
        client=client,
        update=_update("/record_plan Кампания"),
        redis=None,
    )

    assert called["n"] == 0
    texts = [c.kwargs.get("text", "") for c in client.send_message.call_args_list]
    assert any("Redis недоступен" in t for t in texts)


# Анти-регресс: TG публикует ровно в те каналы, которые слушает creator_recorder
def test_channel_names_match_between_tg_and_recorder() -> None:
    from apps.creator_recorder.main import (
        CHANNEL_RECORD_START as RX_START,
    )
    from apps.creator_recorder.main import (
        CHANNEL_RECORD_STOP as RX_STOP,
    )
    from core.telegram.handlers.creator import (
        CHANNEL_RECORD_START as TG_START,
    )
    from core.telegram.handlers.creator import (
        CHANNEL_RECORD_STOP as TG_STOP,
    )

    assert TG_START == RX_START
    assert TG_STOP == RX_STOP

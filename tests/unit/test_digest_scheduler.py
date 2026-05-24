# -*- coding: utf-8 -*-
"""Тесты планировщика daily digest: parse_mode, защита от двойной отправки после рестарта."""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.telegram.client import TelegramBotClient


# Digest_scheduler передаёт parse_mode корректно (не падает с TypeError).
@pytest.mark.asyncio
async def test_send_digest_passes_parse_mode_to_client():
    from core.telegram.digest_scheduler import _send_digest

    client = AsyncMock()
    client.send_message = AsyncMock()

    mock_session = AsyncMock()
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory_fn = MagicMock(return_value=mock_ctx)

    digest_data = {
        "date_str": "22.05.2026",
        "top_offers": [],
        "wasted_alerts": 0,
        "new_offers": [],
        "totals": {"spend": Decimal("0"), "leads": 0, "deps": 0},
    }

    with (
        patch("core.db.get_session_factory", return_value=mock_factory_fn),
        patch(
            "core.telegram.digest_queries.get_digest_data",
            new_callable=AsyncMock,
            return_value=digest_data,
        ),
        patch(
            "core.telegram.digest.render_digest_message",
            return_value="<b>📊 Daily digest</b>",
        ),
    ):
        now = datetime(2026, 5, 23, 6, 0, 0, tzinfo=UTC)
        ok = await _send_digest(client, chat_id="-100", now=now, tz="Europe/Moscow")

    assert ok is True
    client.send_message.assert_called_once()
    kwargs = client.send_message.call_args.kwargs
    assert kwargs["chat_id"] == "-100"
    assert kwargs["parse_mode"] == "HTML"
    assert "Daily digest" in kwargs["text"]


# TelegramBotClient.send_message принимает parse_mode без TypeError и пробрасывает в payload.
@pytest.mark.asyncio
async def test_telegram_bot_client_accepts_parse_mode_kwarg():
    http_client = AsyncMock()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"ok": True, "result": {"message_id": 42}}
    http_client.post = AsyncMock(return_value=response)

    client = TelegramBotClient("token", http_client=http_client)
    try:
        await client.send_message(chat_id="-100", text="<b>x</b>", parse_mode="HTML")
    finally:
        await client.close()

    payload = http_client.post.await_args.kwargs["json"]
    assert payload["parse_mode"] == "HTML"


# Сигнатура send_message содержит параметр parse_mode (защита от регрессии C1).
def test_send_message_signature_has_parse_mode():
    sig = inspect.signature(TelegramBotClient.send_message)
    assert "parse_mode" in sig.parameters


# Повторный вызов digest в один и тот же день не отправляет повторно (after restart):
# при старте last_sent_date загружается из БД, и отправка пропускается.
@pytest.mark.asyncio
async def test_digest_not_resent_after_restart_same_day():
    from core.telegram import digest_scheduler

    client = AsyncMock()
    sent_calls: list[datetime] = []

    async def fake_send_digest(c, *, chat_id, now, tz):
        sent_calls.append(now)
        return True

    # Имитируем рестарт: load_last_sent_date возвращает уже сегодняшнюю дату.
    today_local = date(2026, 5, 22)

    async def fake_load():
        return today_local

    save_calls: list[date] = []

    async def fake_save(d):
        save_calls.append(d)

    tick = 0

    async def fake_sleep(_):
        nonlocal tick
        tick += 1
        if tick >= 3:
            raise asyncio.CancelledError()

    with (
        patch.object(digest_scheduler, "_send_digest", new=fake_send_digest),
        patch.object(digest_scheduler, "_load_last_sent_date", new=fake_load),
        patch.object(digest_scheduler, "_save_last_sent_date", new=fake_save),
        patch.object(digest_scheduler.asyncio, "sleep", new=fake_sleep),
        patch.object(digest_scheduler, "datetime") as mock_dt,
    ):
        # Каждая итерация в 9:00 локального времени того же дня.
        mock_dt.now.return_value = datetime(2026, 5, 22, 9, 0, 0, tzinfo=UTC)

        with pytest.raises(asyncio.CancelledError):
            await digest_scheduler.run_digest_scheduler(
                client, "-100", tz="UTC", hour=9, check_interval=0
            )

    # Главное: ни одного _send_digest за «сегодня» не вызвано.
    assert sent_calls == []
    # И сохранять ничего не должны — отправок не было.
    assert save_calls == []


# При наступлении следующего дня digest отправляется и сохраняется в БД.
@pytest.mark.asyncio
async def test_digest_sent_on_new_day_after_restart():
    from core.telegram import digest_scheduler

    client = AsyncMock()
    sent_calls: list[datetime] = []

    async def fake_send_digest(c, *, chat_id, now, tz):
        sent_calls.append(now)
        return True

    # Имитируем рестарт: вчера слали, сейчас уже наступило завтра.
    yesterday = date(2026, 5, 21)

    async def fake_load():
        return yesterday

    saved: list[date] = []

    async def fake_save(d):
        saved.append(d)

    tick = 0

    async def fake_sleep(_):
        nonlocal tick
        tick += 1
        if tick >= 2:
            raise asyncio.CancelledError()

    with (
        patch.object(digest_scheduler, "_send_digest", new=fake_send_digest),
        patch.object(digest_scheduler, "_load_last_sent_date", new=fake_load),
        patch.object(digest_scheduler, "_save_last_sent_date", new=fake_save),
        patch.object(digest_scheduler.asyncio, "sleep", new=fake_sleep),
        patch.object(digest_scheduler, "datetime") as mock_dt,
    ):
        mock_dt.now.return_value = datetime(2026, 5, 22, 9, 0, 0, tzinfo=UTC)

        with pytest.raises(asyncio.CancelledError):
            await digest_scheduler.run_digest_scheduler(
                client, "-100", tz="UTC", hour=9, check_interval=0
            )

    assert len(sent_calls) == 1
    assert saved == [date(2026, 5, 22)]

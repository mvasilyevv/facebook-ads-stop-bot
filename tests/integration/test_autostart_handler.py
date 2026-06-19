# -*- coding: utf-8 -*-
"""Интеграционные тесты TG-команды /autostart (управление конфигом автостарта)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.scheduler.cabinet_autostart import read_autostart_config
from core.telegram.handlers.autostart import handle_autostart


class _FakeClient:
    """Минимальный фейк TelegramBotClient — фиксирует отправленные сообщения."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(self, **kwargs) -> None:
        self.sent.append(kwargs)


@pytest_asyncio.fixture
async def clean_autostart_config(pg_engine):
    """Чистит system_config.cabinet_autostart до и после теста."""

    async def _trunc():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM system_config WHERE key = 'cabinet_autostart'"))

    await _trunc()
    yield
    await _trunc()


# /autostart без аргументов — показывает текущий конфиг (дефолт: выключен)
@pytest.mark.asyncio
async def test_autostart_show_default(pg_engine, clean_autostart_config) -> None:
    client = _FakeClient()
    await handle_autostart(
        engine=pg_engine,
        client=client,
        chat_id=555,
        message_id=1,
        thread_id=None,
        args_text="",
    )
    assert client.sent, "должен прийти ответ с конфигом"
    text_out = client.sent[-1]["text"]
    assert "выключен" in text_out


# /autostart on включает фичу и пишет в БД
@pytest.mark.asyncio
async def test_autostart_on_enables(pg_engine, clean_autostart_config) -> None:
    client = _FakeClient()
    await handle_autostart(
        engine=pg_engine,
        client=client,
        chat_id=555,
        message_id=1,
        thread_id=None,
        args_text="on",
    )
    config = await read_autostart_config(pg_engine)
    assert config["enabled"] is True


# /autostart HH:MM задаёт время (UTC) в БД (кампании выбираются в UI, не через TG)
@pytest.mark.asyncio
async def test_autostart_set_time(pg_engine, clean_autostart_config) -> None:
    client = _FakeClient()
    await handle_autostart(
        engine=pg_engine,
        client=client,
        chat_id=555,
        message_id=1,
        thread_id=None,
        args_text="06:30",
    )
    config = await read_autostart_config(pg_engine)
    assert config["hour_utc"] == 6
    assert config["minute_utc"] == 30
    # Кампании через TG не задаются — список остаётся как был (пустой).
    assert config["campaign_ids"] == []


# /autostart off выключает, не теряя ранее заданное время
@pytest.mark.asyncio
async def test_autostart_off_keeps_time(pg_engine, clean_autostart_config) -> None:
    client = _FakeClient()
    # Сначала задаём время + включаем.
    await handle_autostart(
        engine=pg_engine,
        client=client,
        chat_id=555,
        message_id=1,
        thread_id=None,
        args_text="07:00",
    )
    await handle_autostart(
        engine=pg_engine,
        client=client,
        chat_id=555,
        message_id=1,
        thread_id=None,
        args_text="on",
    )
    # Теперь выключаем.
    await handle_autostart(
        engine=pg_engine,
        client=client,
        chat_id=555,
        message_id=1,
        thread_id=None,
        args_text="off",
    )
    config = await read_autostart_config(pg_engine)
    assert config["enabled"] is False
    assert config["hour_utc"] == 7, "off не должен обнулять время"


# Невалидный аргумент → подсказка по использованию (конфиг не меняется)
@pytest.mark.asyncio
async def test_autostart_invalid_shows_usage(pg_engine, clean_autostart_config) -> None:
    client = _FakeClient()
    await handle_autostart(
        engine=pg_engine,
        client=client,
        chat_id=555,
        message_id=1,
        thread_id=None,
        args_text="banana",
    )
    text_out = client.sent[-1]["text"]
    assert "Использование" in text_out
    config = await read_autostart_config(pg_engine)
    assert config["enabled"] is False, "невалидная команда не должна включать фичу"

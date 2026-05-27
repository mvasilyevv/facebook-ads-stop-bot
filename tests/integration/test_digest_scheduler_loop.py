# -*- coding: utf-8 -*-
"""Интеграционные тесты apps/digest_scheduler/main.run_one_tick.

Используем fake_redis_client + БД из docker-compose + monkeypatch для TG.
Меняем системное время через явный параметр `now`, чтобы прогонять
сценарии «в окне / вне окна / повтор после отправки».
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text

from apps.digest_scheduler.main import (
    DigestWindow,
    digest_sent_key,
    run_one_tick,
)
from core.crypto import encrypt


@dataclass
class FakeTGClient:
    """Минимальный стаб TelegramBotClient: фиксирует все send_message вызовы."""

    sent: list[dict] = field(default_factory=list)
    closed: bool = False

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        message_thread_id: int | None = None,
        reply_markup: dict | None = None,
        parse_mode: str | None = "HTML",
    ) -> dict:
        self.sent.append(
            {
                "chat_id": chat_id,
                "text": text,
                "thread_id": message_thread_id,
                "parse_mode": parse_mode,
            }
        )
        return {"message_id": len(self.sent)}

    async def close(self) -> None:
        self.closed = True


@pytest_asyncio.fixture
async def clean_loop_tables(pg_engine):
    """Чистит таблицы, которые читает run_one_tick."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            for t in (
                "task_queue",
                "alert_events",
                "ad_metrics",
                "ad_alert_state",
                "fb_ads",
                "fb_adsets",
                "fb_campaigns",
                "offer_rules",
                "offers",
                "telegram_recipients",
                "telegram_config",
            ):
                await conn.execute(text(f"DELETE FROM {t}"))

    await _truncate()
    yield
    await _truncate()


async def _seed_tg_config_and_recipient(pg_engine) -> tuple[int, str]:
    """Кладёт singleton telegram_config с зашифрованным токеном + 1 recipient."""
    bot_token = "1234567:ABC"
    enc = encrypt(bot_token)
    chat_id = 555_000_111
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_config
                    (singleton_key, bot_token_encrypted, chat_id, poller_offset)
                VALUES ('default', :tok, :cid, 0)
                ON CONFLICT (singleton_key) DO UPDATE
                SET bot_token_encrypted = EXCLUDED.bot_token_encrypted,
                    chat_id = EXCLUDED.chat_id
                """
            ),
            {"tok": enc, "cid": chat_id},
        )
        await conn.execute(
            text(
                """
                INSERT INTO telegram_recipients (chat_id, telegram_user_id, role)
                VALUES (:cid, :uid, 'owner')
                """
            ),
            {"cid": chat_id, "uid": 999_111_222},
        )
    return chat_id, bot_token


def _factory_recording(captured: list[FakeTGClient]):
    """Фабрика, которая создаёт новый FakeTGClient и сохраняет его в captured."""

    def _factory(_bot_token: str) -> FakeTGClient:
        client = FakeTGClient()
        captured.append(client)
        return client

    return _factory


# В окне 09:00 UTC и Redis-флаг ещё не выставлен → digest отправляется
@pytest.mark.asyncio
async def test_run_one_tick_sends_in_window(
    pg_engine, fake_redis_client, clean_loop_tables
) -> None:
    chat_id, _ = await _seed_tg_config_and_recipient(pg_engine)
    now = datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc)
    window = DigestWindow(hour=9, minute=0, window_minutes=5)

    captured: list[FakeTGClient] = []
    status = await run_one_tick(
        engine=pg_engine,
        redis_client=fake_redis_client,
        tg_client_factory=_factory_recording(captured),
        now=now,
        window=window,
    )
    assert status == "sent"
    assert len(captured) == 1
    assert len(captured[0].sent) == 1
    assert captured[0].sent[0]["chat_id"] == str(chat_id)
    assert captured[0].sent[0]["parse_mode"] == "HTML"
    assert captured[0].closed is True

    # Флаг проставлен
    assert await fake_redis_client.get(digest_sent_key(now)) == "1"


# Повторный прогон в этом же окне (через минуту) → не шлёт повторно
@pytest.mark.asyncio
async def test_run_one_tick_skips_when_already_sent(
    pg_engine, fake_redis_client, clean_loop_tables
) -> None:
    await _seed_tg_config_and_recipient(pg_engine)
    now1 = datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc)
    now2 = datetime(2026, 5, 27, 9, 1, 0, tzinfo=timezone.utc)
    window = DigestWindow(hour=9, minute=0, window_minutes=5)

    captured: list[FakeTGClient] = []
    factory = _factory_recording(captured)

    first = await run_one_tick(
        engine=pg_engine,
        redis_client=fake_redis_client,
        tg_client_factory=factory,
        now=now1,
        window=window,
    )
    second = await run_one_tick(
        engine=pg_engine,
        redis_client=fake_redis_client,
        tg_client_factory=factory,
        now=now2,
        window=window,
    )

    assert first == "sent"
    assert second == "already_sent"
    # Клиент создан только один раз
    assert len(captured) == 1
    assert len(captured[0].sent) == 1


# 08:00 UTC — до планового времени → out_of_window (catch-up открывается с 09:00)
@pytest.mark.asyncio
async def test_run_one_tick_out_of_window(pg_engine, fake_redis_client, clean_loop_tables) -> None:
    await _seed_tg_config_and_recipient(pg_engine)
    now = datetime(2026, 5, 27, 8, 0, 0, tzinfo=timezone.utc)
    window = DigestWindow(hour=9, minute=0, window_minutes=5)

    captured: list[FakeTGClient] = []
    status = await run_one_tick(
        engine=pg_engine,
        redis_client=fake_redis_client,
        tg_client_factory=_factory_recording(captured),
        now=now,
        window=window,
    )
    assert status == "out_of_window"
    assert captured == []
    assert await fake_redis_client.get(digest_sent_key(now)) is None


# В окне, но telegram_config пустой → ничего не шлём, но и не ставим флаг
# (чтобы при появлении токена на следующем тике дойти до отправки)
@pytest.mark.asyncio
async def test_run_one_tick_no_tg_config(pg_engine, fake_redis_client, clean_loop_tables) -> None:
    now = datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc)
    window = DigestWindow(hour=9, minute=0, window_minutes=5)

    captured: list[FakeTGClient] = []
    status = await run_one_tick(
        engine=pg_engine,
        redis_client=fake_redis_client,
        tg_client_factory=_factory_recording(captured),
        now=now,
        window=window,
    )
    assert status == "no_tg_config"
    assert captured == []
    # Флаг не ставится — иначе пропустим день
    assert await fake_redis_client.get(digest_sent_key(now)) is None


# Конфиг есть, recipient'ов нет → флаг ставим (не задвоим), но никому не пишем
@pytest.mark.asyncio
async def test_run_one_tick_no_recipients(pg_engine, fake_redis_client, clean_loop_tables) -> None:
    # Только telegram_config, без recipient'ов
    enc = encrypt("1234:abc")
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_config
                    (singleton_key, bot_token_encrypted, chat_id, poller_offset)
                VALUES ('default', :tok, :cid, 0)
                ON CONFLICT (singleton_key) DO UPDATE
                SET bot_token_encrypted = EXCLUDED.bot_token_encrypted,
                    chat_id = EXCLUDED.chat_id
                """
            ),
            {"tok": enc, "cid": 123},
        )

    now = datetime(2026, 5, 27, 9, 1, 0, tzinfo=timezone.utc)
    window = DigestWindow(hour=9, minute=0, window_minutes=5)

    captured: list[FakeTGClient] = []
    status = await run_one_tick(
        engine=pg_engine,
        redis_client=fake_redis_client,
        tg_client_factory=_factory_recording(captured),
        now=now,
        window=window,
    )
    assert status == "no_recipients"
    assert captured == []
    assert await fake_redis_client.get(digest_sent_key(now)) == "1"


# Revoked recipient игнорируется
@pytest.mark.asyncio
async def test_run_one_tick_skips_revoked_recipients(
    pg_engine, fake_redis_client, clean_loop_tables
) -> None:
    enc = encrypt("1234:abc")
    chat_active = 100
    chat_revoked = 200
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_config
                    (singleton_key, bot_token_encrypted, chat_id, poller_offset)
                VALUES ('default', :tok, :cid, 0)
                ON CONFLICT (singleton_key) DO UPDATE
                SET bot_token_encrypted = EXCLUDED.bot_token_encrypted,
                    chat_id = EXCLUDED.chat_id
                """
            ),
            {"tok": enc, "cid": chat_active},
        )
        await conn.execute(
            text(
                """
                INSERT INTO telegram_recipients (chat_id, telegram_user_id, role, revoked_at)
                VALUES
                    (:c1, :u1, 'owner', NULL),
                    (:c2, :u2, 'recipient', NOW())
                """
            ),
            {
                "c1": chat_active,
                "u1": uuid.uuid4().int % 1_000_000_000,
                "c2": chat_revoked,
                "u2": uuid.uuid4().int % 1_000_000_000,
            },
        )

    now = datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc)
    window = DigestWindow(hour=9, minute=0, window_minutes=5)

    captured: list[FakeTGClient] = []
    status = await run_one_tick(
        engine=pg_engine,
        redis_client=fake_redis_client,
        tg_client_factory=_factory_recording(captured),
        now=now,
        window=window,
    )
    assert status == "sent"
    sent_chat_ids = {m["chat_id"] for m in captured[0].sent}
    assert sent_chat_ids == {str(chat_active)}

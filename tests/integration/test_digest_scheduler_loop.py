# -*- coding: utf-8 -*-
"""Интеграционные тесты durable digest scheduler.

Scheduler только фиксирует event/deliveries в PostgreSQL-outbox. Telegram I/O
выполняет отдельный delivery worker и здесь намеренно не мокается как success.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import text

from apps.digest_scheduler.main import (
    DigestWindow,
    run_one_tick,
)
from core.config import get_settings
from core.crypto import encrypt
from core.telegram.gateway import telegram_credential_fingerprint


@pytest_asyncio.fixture
async def clean_loop_tables(pg_engine):
    """Чистит таблицы, которые читает run_one_tick."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            for t in (
                "telegram_action_tokens",
                "telegram_message_slots",
                "notification_deliveries",
                "notification_events",
            ):
                await conn.execute(text(f"DELETE FROM {t}"))
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
                "telegram_config",
            ):
                await conn.execute(text(f"DELETE FROM {t}"))
            await conn.execute(text("DELETE FROM telegram_recipient_preferences"))
            await conn.execute(text("DELETE FROM telegram_recipients"))

    await _truncate()
    yield
    await _truncate()


async def _seed_tg_config_and_recipient(pg_engine) -> tuple[int, str]:
    """Кладёт singleton telegram_config с зашифрованным токеном + 1 recipient."""
    bot_token = "1234567:ABC"
    enc = encrypt(bot_token)
    fingerprint = bytes.fromhex(telegram_credential_fingerprint(bot_token))
    chat_id = 555_000_111
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_config
                    (singleton_key, bot_token_encrypted, bot_token_fingerprint,
                     is_enabled, webhook_generation, webhook_applied_generation,
                     webhook_operation, webhook_state, webhook_configured_at)
                VALUES ('default', :tok, :fingerprint,
                        TRUE, 1, 1, 'configure', 'configured', NOW())
                ON CONFLICT (singleton_key) DO UPDATE
                SET bot_token_encrypted = EXCLUDED.bot_token_encrypted,
                    bot_token_fingerprint = EXCLUDED.bot_token_fingerprint,
                    is_enabled = TRUE,
                    webhook_generation = 1,
                    webhook_applied_generation = 1,
                    webhook_operation = 'configure',
                    webhook_state = 'configured',
                    webhook_configured_at = NOW()
                """
            ),
            {"tok": enc, "fingerprint": fingerprint},
        )
        recipient_id = (
            await conn.execute(
                text(
                    """
                INSERT INTO telegram_recipients (chat_id, telegram_user_id, role)
                VALUES (:cid, :uid, 'owner')
                RETURNING id
                """
                ),
                {"cid": chat_id, "uid": 999_111_222},
            )
        ).scalar_one()
        await conn.execute(
            text(
                "INSERT INTO telegram_recipient_preferences (recipient_id, min_severity) "
                "VALUES (:recipient_id, 'ok')"
            ),
            {"recipient_id": recipient_id},
        )
    return chat_id, bot_token


# В окне 09:00 UTC event и delivery атомарно ставятся в durable outbox.
@pytest.mark.asyncio
async def test_run_one_tick_queues_in_window(pg_engine, clean_loop_tables) -> None:
    chat_id, _ = await _seed_tg_config_and_recipient(pg_engine)
    now = datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc)
    window = DigestWindow(hour=9, minute=0)

    status = await run_one_tick(
        engine=pg_engine,
        now=now,
        window=window,
    )
    assert status == "queued"

    async with pg_engine.connect() as conn:
        event = (
            await conn.execute(
                text(
                    "SELECT event_type, audience, facts FROM notification_events "
                    "WHERE dedupe_key = 'daily-digest:2026-05-27'"
                )
            )
        ).one()
        deliveries = (
            await conn.execute(
                text("SELECT state, telegram_chat_id FROM notification_deliveries ORDER BY id")
            )
        ).all()
    assert event.event_type == "daily_digest"
    assert event.audience == "all"
    assert "Spend не подтверждён" in event.facts["summary"]
    assert any("Деньги:" in line for line in event.facts["lines"])
    assert deliveries == [("pending", chat_id)]


# Повторный прогон в этом же окне (через минуту) → не шлёт повторно
@pytest.mark.asyncio
async def test_run_one_tick_skips_when_already_sent(pg_engine, clean_loop_tables) -> None:
    await _seed_tg_config_and_recipient(pg_engine)
    now1 = datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc)
    now2 = datetime(2026, 5, 27, 9, 1, 0, tzinfo=timezone.utc)
    window = DigestWindow(hour=9, minute=0)

    first = await run_one_tick(
        engine=pg_engine,
        now=now1,
        window=window,
    )
    second = await run_one_tick(
        engine=pg_engine,
        now=now2,
        window=window,
    )

    assert first == "queued"
    assert second == "already_sent"
    async with pg_engine.connect() as conn:
        assert (
            await conn.scalar(
                text(
                    "SELECT COUNT(*) FROM notification_events "
                    "WHERE dedupe_key = 'daily-digest:2026-05-27'"
                )
            )
            == 1
        )


# 08:00 UTC — до планового времени → out_of_window (catch-up открывается с 09:00)
@pytest.mark.asyncio
async def test_run_one_tick_out_of_window(pg_engine, clean_loop_tables) -> None:
    await _seed_tg_config_and_recipient(pg_engine)
    now = datetime(2026, 5, 27, 8, 0, 0, tzinfo=timezone.utc)
    window = DigestWindow(hour=9, minute=0)

    status = await run_one_tick(
        engine=pg_engine,
        now=now,
        window=window,
    )
    assert status == "out_of_window"


# Отсутствие bot config не влияет на commit event; credential gate живёт в delivery worker.
@pytest.mark.asyncio
async def test_run_one_tick_queues_without_tg_config(
    pg_engine, clean_loop_tables, monkeypatch
) -> None:
    # Env не является runtime credential source; чистая БД остаётся чистой.
    monkeypatch.setattr(
        get_settings(),
        "telegram_bot_token",
        SecretStr("123456789:RUNTIME_MUST_NOT_IMPORT"),
    )
    now = datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc)
    window = DigestWindow(hour=9, minute=0)

    status = await run_one_tick(
        engine=pg_engine,
        now=now,
        window=window,
    )
    assert status == "queued"
    async with pg_engine.connect() as conn:
        assert await conn.scalar(text("SELECT COUNT(*) FROM notification_events")) == 1
        assert await conn.scalar(text("SELECT COUNT(*) FROM notification_deliveries")) == 0


# Без recipients event остаётся durable, deliveries появятся только для текущей аудитории.
@pytest.mark.asyncio
async def test_run_one_tick_queues_without_recipients(pg_engine, clean_loop_tables) -> None:
    # Только telegram_config, без recipient'ов
    enc = encrypt("1234:abc")
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_config
                    (singleton_key, bot_token_encrypted)
                VALUES ('default', :tok)
                ON CONFLICT (singleton_key) DO UPDATE
                SET bot_token_encrypted = EXCLUDED.bot_token_encrypted
                """
            ),
            {"tok": enc},
        )

    now = datetime(2026, 5, 27, 9, 1, 0, tzinfo=timezone.utc)
    window = DigestWindow(hour=9, minute=0)

    status = await run_one_tick(
        engine=pg_engine,
        now=now,
        window=window,
    )
    assert status == "queued"
    async with pg_engine.connect() as conn:
        assert await conn.scalar(text("SELECT COUNT(*) FROM notification_events")) == 1
        assert await conn.scalar(text("SELECT COUNT(*) FROM notification_deliveries")) == 0


# Revoked recipient игнорируется
@pytest.mark.asyncio
async def test_run_one_tick_skips_revoked_recipients(pg_engine, clean_loop_tables) -> None:
    bot_token = "1234:abc"
    enc = encrypt(bot_token)
    fingerprint = bytes.fromhex(telegram_credential_fingerprint(bot_token))
    chat_active = 100
    chat_revoked = 200
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_config
                    (singleton_key, bot_token_encrypted, bot_token_fingerprint,
                     is_enabled, webhook_generation, webhook_applied_generation,
                     webhook_operation, webhook_state, webhook_configured_at)
                VALUES ('default', :tok, :fingerprint,
                        TRUE, 1, 1, 'configure', 'configured', NOW())
                ON CONFLICT (singleton_key) DO UPDATE
                SET bot_token_encrypted = EXCLUDED.bot_token_encrypted,
                    bot_token_fingerprint = EXCLUDED.bot_token_fingerprint,
                    is_enabled = TRUE,
                    webhook_generation = 1,
                    webhook_applied_generation = 1,
                    webhook_operation = 'configure',
                    webhook_state = 'configured',
                    webhook_configured_at = NOW()
                """
            ),
            {"tok": enc, "fingerprint": fingerprint},
        )
        recipient_rows = (
            await conn.execute(
                text(
                    """
                INSERT INTO telegram_recipients (chat_id, telegram_user_id, role, revoked_at)
                VALUES
                    (:c1, :u1, 'owner', NULL),
                    (:c2, :u2, 'recipient', NOW())
                RETURNING id, chat_id
                """
                ),
                {
                    "c1": chat_active,
                    "u1": uuid.uuid4().int % 1_000_000_000,
                    "c2": chat_revoked,
                    "u2": uuid.uuid4().int % 1_000_000_000,
                },
            )
        ).all()
        active_recipient_id = next(row.id for row in recipient_rows if row.chat_id == chat_active)
        await conn.execute(
            text(
                "INSERT INTO telegram_recipient_preferences (recipient_id, min_severity) "
                "VALUES (:recipient_id, 'ok')"
            ),
            {"recipient_id": active_recipient_id},
        )

    now = datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc)
    window = DigestWindow(hour=9, minute=0)

    status = await run_one_tick(
        engine=pg_engine,
        now=now,
        window=window,
    )
    assert status == "queued"
    async with pg_engine.connect() as conn:
        sent_chat_ids = set(
            (
                await conn.execute(text("SELECT telegram_chat_id FROM notification_deliveries"))
            ).scalars()
        )
    assert sent_chat_ids == {chat_active}

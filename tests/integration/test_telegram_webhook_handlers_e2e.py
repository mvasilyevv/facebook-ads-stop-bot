# -*- coding: utf-8 -*-
"""End-to-end Telegram webhook handler coverage with a real database.

Это «полный» интеграционный тест: всё кроме TG API (respx) и browser-agent (fake stub)
— реальное. Updates приходят через durable webhook inbox; long polling в тесте и runtime нет.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from core.telegram.command_replies import DurableTelegramUpdateClient
from core.telegram.gateway import TelegramHTMLGateway
from core.telegram.handlers.router import handle_update


@pytest_asyncio.fixture
async def authorized_recipient(pg_engine):
    """Создаёт активного recipient'а для webhook command tests."""
    chat_id = 999_888
    user_id = 555_444
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_recipients
                    (chat_id, telegram_user_id, username, role)
                VALUES
                    (:cid, :uid, 'tester', 'owner')
                ON CONFLICT (chat_id, telegram_user_id) DO UPDATE
                    SET role = 'owner', revoked_at = NULL
                """
            ),
            {"cid": chat_id, "uid": user_id},
        )
    yield {"chat_id": chat_id, "user_id": user_id}
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM telegram_recipients WHERE chat_id = :cid AND telegram_user_id = :uid"
            ),
            {"cid": chat_id, "uid": user_id},
        )


# Сценарий: /help создаёт durable HTML reply, не отправляя его из handler.
@pytest.mark.asyncio
async def test_help_command_e2e(pg_engine, tg_respx, authorized_recipient) -> None:
    async with httpx.AsyncClient() as http:
        gateway = TelegramHTMLGateway("FAKE:HELP", http_client=http)
        client = DurableTelegramUpdateClient(gateway)
        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": authorized_recipient["chat_id"], "type": "private"},
                "message_id": 1,
                "from": {
                    "id": authorized_recipient["user_id"],
                    "username": "tester",
                },
                "text": "/help",
            },
        }
        await handle_update(
            engine=pg_engine,
            client=client,
            update=update,
            bot_generation=1,
        )

    assert tg_respx.sent_messages == []
    assert len(client.replies) == 1
    reply = client.replies[0]
    assert reply.chat_id == authorized_recipient["chat_id"]
    assert "веб-интерфейсе" in reply.text
    assert reply.parse_mode == "HTML"
    assert "<b>📖 Telegram</b>" in reply.text


# Сценарий: /start с неактивным кодом → дружелюбный отказ (не крашится)
@pytest.mark.asyncio
async def test_start_with_invalid_invite(pg_engine, tg_respx) -> None:
    async with httpx.AsyncClient() as http:
        gateway = TelegramHTMLGateway("FAKE", http_client=http)
        client = DurableTelegramUpdateClient(gateway)
        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "message_id": 1,
                "from": {"id": 111, "username": "newcomer"},
                "text": "/start TOTALLY_FAKE_CODE_999",
            },
        }
        await handle_update(
            engine=pg_engine,
            client=client,
            update=update,
            bot_generation=1,
        )

    assert tg_respx.sent_messages == []
    sent = client.replies[0].text.lower()
    assert "не подходит" in sent or "устарел" in sent or "использован" in sent


# Сценарий: /start с валидным invite → создаётся recipient + приветствие
@pytest.mark.asyncio
async def test_start_consumes_invite(pg_engine, tg_respx) -> None:
    from datetime import datetime, timedelta, timezone

    code = f"E2EINVITE{uuid.uuid4().hex[:8].upper()}"
    chat_id = 777_666
    user_id = 444_333

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_invites (code, created_by, expires_at)
                VALUES (:c, 'test', :e)
                """
            ),
            {
                "c": code,
                "e": datetime.now(timezone.utc) + timedelta(days=1),
            },
        )

    try:
        async with httpx.AsyncClient() as http:
            gateway = TelegramHTMLGateway("FAKE", http_client=http)
            client = DurableTelegramUpdateClient(gateway)
            update = {
                "update_id": 1,
                "message": {
                    "chat": {"id": chat_id, "type": "private"},
                    "message_id": 1,
                    "from": {"id": user_id, "username": "newuser"},
                    "text": f"/start {code}",
                },
            }
            await handle_update(
                engine=pg_engine,
                client=client,
                update=update,
                bot_generation=1,
            )

        # Проверяем DB-побочные эффекты:
        # 1. invite помечен использованным
        # 2. recipient создан и активен
        async with pg_engine.connect() as conn:
            invite_row = (
                await conn.execute(
                    text("SELECT used_at, used_by FROM telegram_invites WHERE code = :c"),
                    {"c": code},
                )
            ).first()
            rcpt_row = (
                await conn.execute(
                    text(
                        "SELECT username, role, revoked_at FROM telegram_recipients "
                        "WHERE chat_id = :cid AND telegram_user_id = :uid"
                    ),
                    {"cid": chat_id, "uid": user_id},
                )
            ).first()

        assert invite_row[0] is not None  # used_at заполнен
        assert invite_row[1] == str(user_id)
        assert rcpt_row is not None
        assert rcpt_row[0] == "newuser"
        assert rcpt_row[1] == "recipient"
        assert rcpt_row[2] is None  # не revoked

        # Reply intent committed by the webhook worker later crosses sendMessage.
        assert tg_respx.sent_messages == []
        assert any("подключено" in reply.text.lower() for reply in client.replies)
    finally:
        # Cleanup
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM telegram_invites WHERE code = :c"), {"c": code})
            await conn.execute(
                text(
                    "DELETE FROM telegram_recipients "
                    "WHERE chat_id = :cid AND telegram_user_id = :uid"
                ),
                {"cid": chat_id, "uid": user_id},
            )

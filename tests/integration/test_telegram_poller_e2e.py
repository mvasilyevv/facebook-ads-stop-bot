# -*- coding: utf-8 -*-
"""End-to-end Telegram poller: TG update → handler → fake gRPC → real DB → TG response.

Это «полный» интеграционный тест: всё кроме TG API (respx) и browser-agent (fake stub)
— реальное. Если он зелёный, значит цепочка работает и в проде.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from core.telegram.bot_handler import handle_update
from core.telegram.client import TelegramBotClient


@pytest_asyncio.fixture
async def authorized_recipient(pg_engine):
    """Создаёт активного recipient'а — иначе /spy в личке упрётся в access control."""
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


# Сценарий: /help для recipient'a → реальный HTTP POST на sendMessage через respx
@pytest.mark.asyncio
async def test_help_command_e2e(pg_engine, tg_respx, authorized_recipient) -> None:
    async with httpx.AsyncClient() as http:
        client = TelegramBotClient(bot_token="FAKE:HELP", http_client=http)
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
        await handle_update(engine=pg_engine, client=client, update=update)

    assert len(tg_respx.sent_messages) == 1
    sent = tg_respx.sent_messages[0]
    assert sent["chat_id"] == str(authorized_recipient["chat_id"])
    assert "/spy" in sent["text"]
    assert sent.get("parse_mode") == "Markdown"


# Сценарий: /start с неактивным кодом → дружелюбный отказ (не крашится)
@pytest.mark.asyncio
async def test_start_with_invalid_invite(pg_engine, tg_respx) -> None:
    async with httpx.AsyncClient() as http:
        client = TelegramBotClient(bot_token="FAKE", http_client=http)
        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "message_id": 1,
                "from": {"id": 111, "username": "newcomer"},
                "text": "/start TOTALLY_FAKE_CODE_999",
            },
        }
        await handle_update(engine=pg_engine, client=client, update=update)

    sent = tg_respx.sent_messages[0]["text"].lower()
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
            client = TelegramBotClient(bot_token="FAKE", http_client=http)
            update = {
                "update_id": 1,
                "message": {
                    "chat": {"id": chat_id, "type": "private"},
                    "message_id": 1,
                    "from": {"id": user_id, "username": "newuser"},
                    "text": f"/start {code}",
                },
            }
            await handle_update(engine=pg_engine, client=client, update=update)

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

        # TG сообщение: «Подключено как…»
        assert any("подключено" in m["text"].lower() for m in tg_respx.sent_messages)
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


# Сценарий: /spy <slot> <country> для recipient'a → fake gRPC возвращает 2 ads →
# pipeline → 2 TG-сообщения (progress + summary, плюс возможно markdown)
@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_spy_full_flow(
    pg_engine,
    tg_respx,
    authorized_recipient,
    fake_ad_lib_client,
    fake_ad_lib_scenario,
    clean_ad_library_tables,
) -> None:
    fake_ad_lib_scenario.ad_count = 2
    fake_ad_lib_scenario.ads = [
        {
            "ad_archive_id": "5555",
            "page_id": "11",
            "page_name": "Casino A",
            "snapshot": {
                "page_name": "Casino A",
                "body": {"text": "Aviator best slot"},
            },
            "start_date": "2026-04-15",
        },
        {
            "ad_archive_id": "6666",
            "page_id": "22",
            "page_name": "Casino B",
            "snapshot": {
                "page_name": "Casino B",
                "body": {"text": "Aviator demo"},
            },
            "start_date": "2026-05-20",
        },
    ]

    async with httpx.AsyncClient() as http:
        client = TelegramBotClient(bot_token="FAKE:SPY", http_client=http)
        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": authorized_recipient["chat_id"], "type": "private"},
                "message_id": 1,
                "from": {
                    "id": authorized_recipient["user_id"],
                    "username": "tester",
                },
                "text": "/spy aviator KE",
            },
        }
        await handle_update(engine=pg_engine, client=client, update=update)

        # Дождаться pipeline background task'а
        pending = {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}
        if pending:
            await asyncio.wait(pending, timeout=25.0)

    # Проверки на TG-стороне
    texts = [m["text"] for m in tg_respx.sent_messages]
    # 1. Был progress message «Сканирую…»
    assert any("Сканирую" in t for t in texts), f"no progress msg in {texts!r}"
    # 2. Был summary
    assert any("aviator" in t.lower() and "KE" in t for t in texts), f"no summary in {texts!r}"

    # Проверки на DB-стороне: pipeline создал scan + 2 snapshots + 2 tier + report
    async with pg_engine.connect() as conn:
        scan_count = (
            await conn.execute(text("SELECT COUNT(*) FROM ad_library_scan WHERE slot = 'aviator'"))
        ).scalar()
        ad_count = (await conn.execute(text("SELECT COUNT(*) FROM ad_library_ad"))).scalar()
        snap_count = (await conn.execute(text("SELECT COUNT(*) FROM ad_library_snapshot"))).scalar()
        tier_count = (await conn.execute(text("SELECT COUNT(*) FROM ad_library_tier"))).scalar()

    assert scan_count == 1
    assert ad_count == 2
    assert snap_count == 2
    assert tier_count == 2

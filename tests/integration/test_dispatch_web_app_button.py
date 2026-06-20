# -*- coding: utf-8 -*-
"""dispatch добавляет web_app deep-link кнопку под алертом при заданном web_app_url.

Волна 3: при наличии https web_app_url в system_config клавиатура алерта содержит
кнопку «🔎 Открыть в Mini App» с URL {base}/ads/{fb_ad_id}; при отсутствии — нет.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.telegram.alert_dispatcher import dispatch_pending_alerts
from core.telegram.web_app_url import save_web_app_url


async def _seed_tg_config_no_chat(conn) -> None:
    from core.crypto import encrypt

    enc = encrypt("TEST_BOT_TOKEN_FAKE")
    await conn.execute(
        text(
            """
            INSERT INTO telegram_config
                (singleton_key, bot_token_encrypted, chat_id, poller_offset)
            VALUES ('default', :tok, NULL, 0)
            ON CONFLICT (singleton_key) DO UPDATE
            SET bot_token_encrypted = EXCLUDED.bot_token_encrypted, chat_id = NULL
            """
        ),
        {"tok": enc},
    )


@pytest_asyncio.fixture
async def _seed(pg_engine):
    """1 recipient + fb_ad '900' + STOP alert_event (scan_id=31)."""
    ad_id = uuid.uuid4()
    tok = uuid.uuid4()
    async with pg_engine.begin() as conn:
        for t in (
            "telegram_message_refs",
            "telegram_recipients",
            "alert_events",
            "fb_ads",
            "fb_adsets",
            "fb_campaigns",
            "telegram_config",
        ):
            await conn.execute(text(f"DELETE FROM {t}"))
        await _seed_tg_config_no_chat(conn)
        await conn.execute(
            text(
                "INSERT INTO telegram_recipients (id, chat_id, telegram_user_id, role) "
                "VALUES (gen_random_uuid(), 111, 111, 'recipient')"
            )
        )
        cid_c = uuid.uuid4()
        sid = uuid.uuid4()
        await conn.execute(
            text(
                "INSERT INTO fb_campaigns (id, fb_campaign_id, campaign_name, last_seen_at) "
                "VALUES (:i, 'c', 'CR2|KE', NOW())"
            ),
            {"i": cid_c},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_adsets (id, fb_adset_id, adset_name, campaign_id, last_seen_at) "
                "VALUES (:i, 's', 'EQ', :c, NOW())"
            ),
            {"i": sid, "c": cid_c},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_ads (id, fb_ad_id, ad_name, adset_id, last_seen_at) "
                "VALUES (:i, '900', 'Ad', :s, NOW())"
            ),
            {"i": ad_id, "s": sid},
        )
        await conn.execute(
            text(
                "INSERT INTO alert_events "
                "(id, ad_id, stage, state, matched_rule_codes, metrics_json, "
                "open_state_token, scan_id, created_at) "
                "VALUES (gen_random_uuid(), :ad, 'stop', 'stop_sent', '[]'::jsonb, "
                "'{}'::jsonb, :tok, 31, NOW())"
            ),
            {"ad": ad_id, "tok": tok},
        )
    return {"ad_id": ad_id}


def _reply_markup(client) -> dict | None:
    """reply_markup из последнего вызова send_message."""
    return client.send_message.await_args.kwargs["reply_markup"]


# web_app_url задан → клавиатура содержит web_app deep-link кнопку на /ads/900
@pytest.mark.asyncio
async def test_web_app_button_present_when_url_set(pg_engine, _seed):
    await save_web_app_url(pg_engine, "https://h.ts.net/tma")
    client = AsyncMock()
    client.send_message = AsyncMock(return_value={"message_id": 5})

    await dispatch_pending_alerts(pg_engine, client=client, scan_id=31, redis_client=None)

    rows = _reply_markup(client)["inline_keyboard"]
    assert rows[0][0]["text"] == "🔎 Открыть в Mini App"
    assert rows[0][0]["web_app"]["url"] == "https://h.ts.net/tma/ads/900"
    assert rows[1][0]["text"] == "🛑 Отключить"


# web_app_url пуст → web_app-кнопки нет, только «Отключить» (graceful)
@pytest.mark.asyncio
async def test_web_app_button_absent_when_url_unset(pg_engine, _seed):
    await save_web_app_url(pg_engine, None)
    client = AsyncMock()
    client.send_message = AsyncMock(return_value={"message_id": 5})

    await dispatch_pending_alerts(pg_engine, client=client, scan_id=31, redis_client=None)

    rows = _reply_markup(client)["inline_keyboard"]
    assert len(rows) == 1
    assert rows[0][0]["text"] == "🛑 Отключить"

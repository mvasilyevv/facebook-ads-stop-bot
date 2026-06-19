# -*- coding: utf-8 -*-
"""retry-sweep ресендит alert_event без message_ref и не трогает уже доставленный."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.telegram.alert_dispatcher import sweep_orphan_alerts


async def _seed_tg_config(conn) -> None:
    """Вставляет минимальный telegram_config с chat_id для тестов sweep."""
    from core.crypto import encrypt

    enc = encrypt("TEST_BOT_TOKEN_FAKE")
    await conn.execute(
        text(
            """
            INSERT INTO telegram_config
                (singleton_key, bot_token_encrypted, chat_id,
                 forum_warning_thread_id, forum_stop_thread_id, poller_offset)
            VALUES ('default', :tok, -1001234567890, NULL, NULL, 0)
            ON CONFLICT (singleton_key) DO UPDATE
            SET bot_token_encrypted = EXCLUDED.bot_token_encrypted,
                chat_id = EXCLUDED.chat_id
            """
        ),
        {"tok": enc},
    )


@pytest_asyncio.fixture
async def _seed_orphan(pg_engine):
    """Один fb_ad + STOP alert_event БЕЗ message_ref (осиротевший, в 24h окне)."""
    ad_id = uuid.uuid4()
    token = uuid.uuid4()
    async with pg_engine.begin() as conn:
        for t in (
            "telegram_message_refs",
            "alert_events",
            "fb_ads",
            "fb_adsets",
            "fb_campaigns",
        ):
            await conn.execute(text(f"DELETE FROM {t}"))
        await _seed_tg_config(conn)
        cid = uuid.uuid4()
        sid = uuid.uuid4()
        await conn.execute(
            text(
                "INSERT INTO fb_campaigns (id, fb_campaign_id, campaign_name, last_seen_at) "
                "VALUES (:i,'c1','CR2 | KE', NOW())"
            ),
            {"i": str(cid)},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_adsets (id, fb_adset_id, adset_name, campaign_id, last_seen_at) "
                "VALUES (:i,'s1','EQ', :c, NOW())"
            ),
            {"i": str(sid), "c": str(cid)},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_ads (id, fb_ad_id, ad_name, adset_id, last_seen_at) "
                "VALUES (:i,'900','Ad', :s, NOW())"
            ),
            {"i": str(ad_id), "s": str(sid)},
        )
        await conn.execute(
            text(
                "INSERT INTO alert_events (id, ad_id, stage, state, matched_rule_codes, "
                "metrics_json, open_state_token, scan_id, created_at) "
                "VALUES (gen_random_uuid(), :ad, 'stop', 'stop_sent', '[]'::jsonb, "
                "'{}'::jsonb, :tok, 1, NOW())"
            ),
            {"ad": str(ad_id), "tok": str(token)},
        )
    return {"ad_id": ad_id, "token": token}


# Осиротевший event ресендится; message_ref создан; повторный sweep → 0
@pytest.mark.asyncio
async def test_sweep_resends_orphan(pg_engine, _seed_orphan):
    client = AsyncMock()
    client.send_message = AsyncMock(return_value={"message_id": 555})
    res = await sweep_orphan_alerts(pg_engine, client=client, redis_client=None, hours=24)
    assert res["sent"] == 1
    client.send_message.assert_awaited_once()
    async with pg_engine.connect() as conn:
        n = (
            await conn.execute(
                text("SELECT count(*) FROM telegram_message_refs WHERE message_id = 555")
            )
        ).scalar()
    assert n == 1
    # Второй прогон — уже доставлено, ресенда нет
    res2 = await sweep_orphan_alerts(pg_engine, client=client, redis_client=None, hours=24)
    assert res2["sent"] == 0


# Алерт старше hours-окна не попадает в sweep
@pytest.mark.asyncio
async def test_sweep_ignores_old_events(pg_engine):
    ad_id = uuid.uuid4()
    token = uuid.uuid4()
    async with pg_engine.begin() as conn:
        for t in (
            "telegram_message_refs",
            "alert_events",
            "fb_ads",
            "fb_adsets",
            "fb_campaigns",
        ):
            await conn.execute(text(f"DELETE FROM {t}"))
        await _seed_tg_config(conn)
        cid = uuid.uuid4()
        sid = uuid.uuid4()
        await conn.execute(
            text(
                "INSERT INTO fb_campaigns (id, fb_campaign_id, campaign_name, last_seen_at) "
                "VALUES (:i,'c2','CR2 | OLD', NOW())"
            ),
            {"i": str(cid)},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_adsets (id, fb_adset_id, adset_name, campaign_id, last_seen_at) "
                "VALUES (:i,'s2','OLD', :c, NOW())"
            ),
            {"i": str(sid), "c": str(cid)},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_ads (id, fb_ad_id, ad_name, adset_id, last_seen_at) "
                "VALUES (:i,'901','OldAd', :s, NOW())"
            ),
            {"i": str(ad_id), "s": str(sid)},
        )
        # alert_event с created_at за пределами окна (48 часов назад)
        await conn.execute(
            text(
                "INSERT INTO alert_events (id, ad_id, stage, state, matched_rule_codes, "
                "metrics_json, open_state_token, scan_id, created_at) "
                "VALUES (gen_random_uuid(), :ad, 'stop', 'stop_sent', '[]'::jsonb, "
                "'{}'::jsonb, :tok, 2, NOW() - INTERVAL '48 hours')"
            ),
            {"ad": str(ad_id), "tok": str(token)},
        )

    client = AsyncMock()
    client.send_message = AsyncMock(return_value={"message_id": 999})
    # hours=1 — событие 48ч назад не должно попасть
    res = await sweep_orphan_alerts(pg_engine, client=client, redis_client=None, hours=1)
    assert res["sent"] == 0
    client.send_message.assert_not_awaited()


# Алерт с open_state_token=NULL не ресендится через sweep (нет incident_key для сопоставления)
@pytest.mark.asyncio
async def test_sweep_skips_null_token(pg_engine):
    ad_id = uuid.uuid4()
    async with pg_engine.begin() as conn:
        for t in (
            "telegram_message_refs",
            "alert_events",
            "fb_ads",
            "fb_adsets",
            "fb_campaigns",
        ):
            await conn.execute(text(f"DELETE FROM {t}"))
        await _seed_tg_config(conn)
        cid = uuid.uuid4()
        sid = uuid.uuid4()
        await conn.execute(
            text(
                "INSERT INTO fb_campaigns (id, fb_campaign_id, campaign_name, last_seen_at) "
                "VALUES (:i,'c3','CR2 | NTKN', NOW())"
            ),
            {"i": str(cid)},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_adsets (id, fb_adset_id, adset_name, campaign_id, last_seen_at) "
                "VALUES (:i,'s3','NTKN', :c, NOW())"
            ),
            {"i": str(sid), "c": str(cid)},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_ads (id, fb_ad_id, ad_name, adset_id, last_seen_at) "
                "VALUES (:i,'902','NtknAd', :s, NOW())"
            ),
            {"i": str(ad_id), "s": str(sid)},
        )
        # open_state_token IS NULL
        await conn.execute(
            text(
                "INSERT INTO alert_events (id, ad_id, stage, state, matched_rule_codes, "
                "metrics_json, open_state_token, scan_id, created_at) "
                "VALUES (gen_random_uuid(), :ad, 'warning', 'warning_sent', '[]'::jsonb, "
                "'{}'::jsonb, NULL, 3, NOW())"
            ),
            {"ad": str(ad_id)},
        )

    client = AsyncMock()
    # NULL-token алерты пропускаются (нет incident_key для NOT EXISTS)
    res = await sweep_orphan_alerts(pg_engine, client=client, redis_client=None, hours=24)
    assert res["sent"] == 0

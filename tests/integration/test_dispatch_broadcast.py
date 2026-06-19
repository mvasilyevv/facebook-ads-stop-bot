# -*- coding: utf-8 -*-
"""dispatch рассылает алерт ВСЕМ recipients → N message_refs (per-chat дедуп).

Волна 2, Task 3: вместо одного config.chat_id (супергруппа) — рассылка всем
активным recipients в личку. Дедуп per-chat работает из коробки через UNIQUE
(chat_id, ad_id, incident_key, stream_kind) в telegram_message_refs.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.telegram.alert_dispatcher import dispatch_pending_alerts, sweep_orphan_alerts


async def _seed_tg_config_no_chat(conn) -> None:
    """telegram_config БЕЗ chat_id — чтобы проверить новое поведение через recipients."""
    from core.crypto import encrypt

    enc = encrypt("TEST_BOT_TOKEN_FAKE")
    await conn.execute(
        text(
            """
            INSERT INTO telegram_config
                (singleton_key, bot_token_encrypted, chat_id, poller_offset)
            VALUES ('default', :tok, NULL, 0)
            ON CONFLICT (singleton_key) DO UPDATE
            SET bot_token_encrypted = EXCLUDED.bot_token_encrypted,
                chat_id = NULL
            """
        ),
        {"tok": enc},
    )


@pytest_asyncio.fixture
async def _seed(pg_engine):
    """2 recipient'а + fb_ad + STOP alert_event (scan_id=7)."""
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

        # config без chat_id — только токен; рассылка через recipients
        await _seed_tg_config_no_chat(conn)

        # 2 активных recipient'а в личке
        for cid in (111, 222):
            await conn.execute(
                text(
                    "INSERT INTO telegram_recipients "
                    "(id, chat_id, telegram_user_id, role) "
                    "VALUES (gen_random_uuid(), :c, :c, 'recipient')"
                ),
                {"c": cid},
            )

        # иерархия campaign → adset → ad
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
                "'{}'::jsonb, :tok, 7, NOW())"
            ),
            {"ad": ad_id, "tok": tok},
        )
    return {"ad_id": ad_id}


# 2 recipients → 2 message_refs, повторный dispatch не задваивает
@pytest.mark.asyncio
async def test_broadcast_two_recipients(pg_engine, _seed):
    """2 активных recipient'а получают алерт каждый — итого 2 send_message и 2 message_refs."""
    client = AsyncMock()
    client.send_message = AsyncMock(return_value={"message_id": 5})

    # config.chat_id=NULL — раньше это был skip; теперь шлём по recipients
    await dispatch_pending_alerts(pg_engine, client=client, scan_id=7, redis_client=None)
    assert client.send_message.await_count == 2, (
        f"Ожидали 2 send_message (по одному на recipient), получили {client.send_message.await_count}"
    )

    async with pg_engine.connect() as conn:
        n = (await conn.execute(text("SELECT count(*) FROM telegram_message_refs"))).scalar()
    assert n == 2, f"Ожидали 2 message_refs (per-chat дедуп), нашли {n}"

    # повторный dispatch — дедуп per-chat, 0 новых отправок
    client.send_message.reset_mock()
    await dispatch_pending_alerts(pg_engine, client=client, scan_id=7, redis_client=None)
    assert client.send_message.await_count == 0, "Повторный dispatch задвоил сообщения!"


@pytest_asyncio.fixture
async def _seed_sweep(pg_engine):
    """2 recipient'а + осиротевший STOP alert_event (без message_refs)."""
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

        for cid in (333, 444):
            await conn.execute(
                text(
                    "INSERT INTO telegram_recipients "
                    "(id, chat_id, telegram_user_id, role) "
                    "VALUES (gen_random_uuid(), :c, :c, 'recipient')"
                ),
                {"c": cid},
            )

        cid_c = uuid.uuid4()
        sid = uuid.uuid4()
        await conn.execute(
            text(
                "INSERT INTO fb_campaigns (id, fb_campaign_id, campaign_name, last_seen_at) "
                "VALUES (:i, 'cx', 'CR2|KE|sweep', NOW())"
            ),
            {"i": cid_c},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_adsets (id, fb_adset_id, adset_name, campaign_id, last_seen_at) "
                "VALUES (:i, 'sx', 'EQx', :c, NOW())"
            ),
            {"i": sid, "c": cid_c},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_ads (id, fb_ad_id, ad_name, adset_id, last_seen_at) "
                "VALUES (:i, '901', 'AdSweep', :s, NOW())"
            ),
            {"i": ad_id, "s": sid},
        )
        # осиротевший алерт: нет message_refs ни для кого
        await conn.execute(
            text(
                "INSERT INTO alert_events "
                "(id, ad_id, stage, state, matched_rule_codes, metrics_json, "
                "open_state_token, scan_id, created_at) "
                "VALUES (gen_random_uuid(), :ad, 'stop', 'stop_sent', '[]'::jsonb, "
                "'{}'::jsonb, :tok, 8, NOW())"
            ),
            {"ad": ad_id, "tok": tok},
        )
    return {"ad_id": ad_id, "tok": tok}


# sweep ресендит осиротевший алерт ВСЕМ recipients (per-recipient NOT EXISTS)
@pytest.mark.asyncio
async def test_sweep_broadcasts_orphan_to_all_recipients(pg_engine, _seed_sweep):
    """sweep_orphan_alerts ресендит осиротевший алерт каждому из 2 recipients."""
    client = AsyncMock()
    client.send_message = AsyncMock(return_value={"message_id": 77})

    await sweep_orphan_alerts(pg_engine, client=client, redis_client=None, hours=24)
    assert client.send_message.await_count == 2, (
        f"sweep должен отправить 2 сообщения (по одному на recipient), "
        f"отправил {client.send_message.await_count}"
    )

    async with pg_engine.connect() as conn:
        n = (await conn.execute(text("SELECT count(*) FROM telegram_message_refs"))).scalar()
    assert n == 2, f"После sweep ожидали 2 message_refs, нашли {n}"

    # повторный sweep — дедуп, 0 новых
    client.send_message.reset_mock()
    await sweep_orphan_alerts(pg_engine, client=client, redis_client=None, hours=24)
    assert client.send_message.await_count == 0, "Повторный sweep задвоил!"


# sweep с одним уже доставленным recipient'ом — второй получает, первый дедупируется
@pytest.mark.asyncio
async def test_sweep_partial_delivery(pg_engine, _seed_sweep):
    """Если первый recipient уже получил (есть message_ref), sweep доставляет только второму."""
    # Доставляем только recipient 333 (вставляем message_ref вручную)
    async with pg_engine.begin() as conn:
        # Получаем open_state_token из alert_events
        row = (
            await conn.execute(
                text("SELECT e.id, e.ad_id, e.open_state_token FROM alert_events e LIMIT 1")
            )
        ).first()
        if row is None:
            pytest.skip("seed не применился")
        event_id, ad_id, open_token = row
        # Создаём pre-delivered ref для recipient 333
        await conn.execute(
            text(
                "INSERT INTO telegram_message_refs "
                "(chat_id, ad_id, incident_key, stream_kind, message_id, sent_at) "
                "VALUES (333, :aid, :ik, 'stop', 99, NOW())"
            ),
            {"aid": ad_id, "ik": str(open_token)},
        )

    client = AsyncMock()
    client.send_message = AsyncMock(return_value={"message_id": 88})

    await sweep_orphan_alerts(pg_engine, client=client, redis_client=None, hours=24)
    # только recipient 444 должен получить (333 — дедуп)
    assert client.send_message.await_count == 1, (
        f"Ожидали 1 отправку (444), получили {client.send_message.await_count}"
    )

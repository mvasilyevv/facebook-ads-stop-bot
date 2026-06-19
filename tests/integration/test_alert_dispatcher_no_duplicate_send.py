# -*- coding: utf-8 -*-
"""Integration: pre-claim защищает от двойного TG-сообщения при гонке dispatch'ов.

Сценарий: два observer-loop'а параллельно вызывают dispatch_pending_alerts на одном
alert_event. Без pre-claim паттерна оба SELECT'а вернут «ref нет» → оба send'а
улетят в TG → пользователь получит дубль. С pre-claim INSERT ON CONFLICT DO NOTHING
один из dispatch'ей не получит RETURNING → пропускает send'.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from core.telegram.alert_dispatcher import dispatch_pending_alerts
from core.telegram.client import TelegramBotClient

# Волна 2: dispatch рассылает по telegram_recipients. Scoped chat_id для этого файла.
_NO_DUP_RECIPIENT_CHAT_ID = 66554433


@pytest_asyncio.fixture
async def clean_dispatch_tables(pg_engine):
    """Очистка таблиц для теста гонки."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            for t in (
                "telegram_message_refs",
                "telegram_recipients",
                "alert_events",
                "ad_alert_state",
                "fb_ads",
                "fb_adsets",
                "fb_campaigns",
                "offers",
            ):
                await conn.execute(text(f"DELETE FROM {t}"))

    await _truncate()
    yield
    await _truncate()


@pytest_asyncio.fixture
async def alert_event_fixture(pg_engine, clean_dispatch_tables):
    """Создаёт offer→campaign→adset→ad + один alert_event для dispatch'а."""
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]
    open_token = uuid.uuid4()
    scan_id = 12345

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
            {"i": offer_id, "c": f"RC_{suffix}", "n": "Race test offer"},
        )
        await conn.execute(
            text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
            {"i": campaign_id, "n": f"CMP_{suffix}", "o": offer_id},
        )
        await conn.execute(
            text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
            {"i": adset_id, "c": campaign_id, "n": f"ADSET_{suffix}"},
        )
        await conn.execute(
            text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
            {"i": ad_id, "a": adset_id, "f": f"23700{suffix}", "n": f"AD_{suffix}"},
        )
        await conn.execute(
            text(
                """
                INSERT INTO alert_events
                    (ad_id, stage, state, matched_rule_codes, metrics_json,
                     open_state_token, scan_id)
                VALUES
                    (:aid, 'stop', 'stop_sent',
                     CAST(:mrc AS JSONB), CAST(:m AS JSONB), :tok, :sid)
                """
            ),
            {
                "aid": ad_id,
                "mrc": json.dumps(["cpc_stop"]),
                "m": json.dumps({"spend": "25.0", "deposits": 0}),
                "tok": open_token,
                "sid": scan_id,
            },
        )
        # Волна 2: рассылка по recipients; сеем одного для pre-claim race-теста.
        await conn.execute(
            text(
                "INSERT INTO telegram_recipients "
                "(id, chat_id, telegram_user_id, role) "
                "VALUES (gen_random_uuid(), :c, :c, 'recipient')"
            ),
            {"c": _NO_DUP_RECIPIENT_CHAT_ID},
        )

    yield {"ad_id": ad_id, "scan_id": scan_id, "open_token": open_token}


# Сценарий: два параллельных dispatch'а одного scan_id → ровно 1 sendMessage и 1 ref
@pytest.mark.asyncio
async def test_parallel_dispatch_sends_message_once(
    pg_engine,
    alert_event_fixture,
    seeded_telegram_config,
    tg_respx,
) -> None:
    scan_id = alert_event_fixture["scan_id"]

    async def _one_dispatch():
        async with httpx.AsyncClient() as http:
            client = TelegramBotClient(bot_token="FAKE", http_client=http)
            return await dispatch_pending_alerts(pg_engine, client=client, scan_id=scan_id)

    # Гонка двух параллельных dispatch'ей на одних и тех же событиях
    c1, c2 = await asyncio.gather(_one_dispatch(), _one_dispatch())

    # Один claim'нул и отправил, второй увидел conflict → пропустил send
    sent_total = c1["sent"] + c2["sent"]
    skipped_total = c1["skipped_duplicates"] + c2["skipped_duplicates"]
    assert sent_total == 1
    assert skipped_total == 1

    # В TG ушло РОВНО одно сообщение
    assert len(tg_respx.sent_messages) == 1

    # message_ref ровно один
    async with pg_engine.connect() as conn:
        n_refs = (await conn.execute(text("SELECT COUNT(*) FROM telegram_message_refs"))).scalar()
    assert n_refs == 1

    # message_id у ref'а уже реальный (не sentinel 0) — UPDATE прошёл
    async with pg_engine.connect() as conn:
        mid = (await conn.execute(text("SELECT message_id FROM telegram_message_refs"))).scalar()
    assert mid > 0


# Сценарий: TG вернул message_id=0 → sentinel остаётся, повторный dispatch пропускается
@pytest.mark.asyncio
async def test_sentinel_message_id_zero_blocks_second_dispatch(
    pg_engine,
    alert_event_fixture,
    seeded_telegram_config,
) -> None:
    """HIGH #10: если sendMessage успешен но message_id=0 — sentinel (message_id=0)
    остаётся в telegram_message_refs. Повторный dispatch для того же события
    натыкается на ON CONFLICT DO NOTHING → пропускает send (один send, не два).
    """
    import respx
    from httpx import Response

    scan_id = alert_event_fixture["scan_id"]

    # Первый вызов: sendMessage возвращает message_id=0 (сервер не вернул реальный id)
    with respx.mock(assert_all_called=False) as mock:
        call_count = {"n": 0}

        def _handler(request):
            call_count["n"] += 1
            return Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "message_id": 0,  # sentinel — нет реального id
                        "chat": {"id": -1001234567890, "type": "supergroup"},
                        "date": 0,
                        "text": "test",
                    },
                },
            )

        mock.post(url__regex=r"https://api\.telegram\.org/bot[^/]+/sendMessage").mock(
            side_effect=_handler
        )

        async with httpx.AsyncClient() as http:
            client = TelegramBotClient(bot_token="FAKE", http_client=http)
            c1 = await dispatch_pending_alerts(pg_engine, client=client, scan_id=scan_id)

    assert c1["sent"] == 1, "Первый dispatch должен посчитать sent=1 (send прошёл)"
    assert call_count["n"] == 1, "sendMessage должен был вызван ровно один раз"

    # Sentinel должен остаться в БД с message_id=0
    async with pg_engine.connect() as conn:
        ref_row = (
            await conn.execute(text("SELECT message_id FROM telegram_message_refs LIMIT 1"))
        ).first()
    assert ref_row is not None, "Sentinel ref должен быть в БД"
    assert ref_row[0] == 0, f"message_id должен остаться 0 (sentinel), но = {ref_row[0]}"

    # Второй dispatch: ON CONFLICT DO NOTHING → пропустит INSERT, не пошлёт повторно
    with respx.mock(assert_all_called=False) as mock2:
        call_count2 = {"n": 0}

        def _handler2(request):
            call_count2["n"] += 1
            return Response(200, json={"ok": True, "result": {"message_id": 9999}})

        mock2.post(url__regex=r"https://api\.telegram\.org/bot[^/]+/sendMessage").mock(
            side_effect=_handler2
        )

        async with httpx.AsyncClient() as http:
            client = TelegramBotClient(bot_token="FAKE", http_client=http)
            c2 = await dispatch_pending_alerts(pg_engine, client=client, scan_id=scan_id)

    assert c2["skipped_duplicates"] == 1, (
        "Второй dispatch должен пропустить событие через sentinel dedup"
    )
    assert call_count2["n"] == 0, "sendMessage не должен быть вызван повторно"


# Сценарий: send упал → DELETE pre-claim записи, чтобы retry мог переслать
@pytest.mark.asyncio
async def test_failed_send_releases_claim(
    pg_engine, alert_event_fixture, seeded_telegram_config
) -> None:
    import respx
    from httpx import Response

    scan_id = alert_event_fixture["scan_id"]

    with respx.mock(assert_all_called=False) as mock:
        mock.post(url__regex=r"https://api\.telegram\.org/bot[^/]+/sendMessage").mock(
            return_value=Response(400, json={"ok": False, "description": "bad request"})
        )

        async with httpx.AsyncClient() as http:
            client = TelegramBotClient(bot_token="FAKE", http_client=http)
            counters = await dispatch_pending_alerts(pg_engine, client=client, scan_id=scan_id)

    assert counters["sent"] == 0
    assert counters["errors"] == 1

    # claim был освобождён — refs пустые, ретрай сможет послать заново
    async with pg_engine.connect() as conn:
        n_refs = (await conn.execute(text("SELECT COUNT(*) FROM telegram_message_refs"))).scalar()
    assert n_refs == 0

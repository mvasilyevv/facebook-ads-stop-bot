# -*- coding: utf-8 -*-
"""Интеграционный: end-to-end отправка алертов observer'a → TG (через respx + real DB).

Покрывает критический контракт «алерт ушёл в TG ровно один раз»:
- INSERT alert_event → dispatch → sendMessage через respx
- Идемпотентность через telegram_message_refs (повторный вызов → 0 отправок)
- Корректный thread_id для warning vs stop
- HTML escape, inline-кнопки в payload
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from core.telegram.alert_dispatcher import dispatch_pending_alerts
from core.telegram.client import TelegramBotClient

# Волна 2: DM-модель — алерты уходят в личку recipient'а, а не в супергруппу.
# chat_id одного тестового recipient'а (уникальный, не пересекается с другими тестами).
_DISPATCHER_TEST_RECIPIENT_CHAT_ID = 55443322


@pytest_asyncio.fixture
async def offer_and_ad(pg_engine):
    """Создаёт всю иерархию offer→campaign→adset→ad + одного активного recipient'а.

    telegram_config seed'ится отдельной fixture `seeded_telegram_config`.
    Волна 2: dispatch рассылает по telegram_recipients, а не по config.chat_id.
    """
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
            {"i": offer_id, "c": f"TST_{suffix}", "n": "Test offer"},
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
            {
                "i": ad_id,
                "a": adset_id,
                "f": f"23000{suffix}",
                "n": f"AD_{suffix}",
            },
        )
        # Сеем одного recipient'а (scoped chat_id, чтобы не конфликтовать с другими тестами)
        await conn.execute(
            text(
                "INSERT INTO telegram_recipients "
                "(id, chat_id, telegram_user_id, role) "
                "VALUES (gen_random_uuid(), :c, :c, 'recipient') "
                "ON CONFLICT (chat_id, telegram_user_id) DO NOTHING"
            ),
            {"c": _DISPATCHER_TEST_RECIPIENT_CHAT_ID},
        )

    yield {
        "offer_id": offer_id,
        "campaign_id": campaign_id,
        "adset_id": adset_id,
        "ad_id": ad_id,
        "fb_ad_id": f"23000{suffix}",
    }

    # Cleanup
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_message_refs WHERE ad_id = :i"), {"i": ad_id})
        await conn.execute(text("DELETE FROM alert_events WHERE ad_id = :i"), {"i": ad_id})
        await conn.execute(text("DELETE FROM offers WHERE id = :i"), {"i": offer_id})
        await conn.execute(
            text("DELETE FROM telegram_recipients WHERE chat_id = :c"),
            {"c": _DISPATCHER_TEST_RECIPIENT_CHAT_ID},
        )


async def _insert_alert(
    engine,
    *,
    ad_id,
    stage: str,
    scan_id: int,
    token: uuid.UUID,
    rule_codes: list[str],
    metrics: dict,
) -> None:
    state = "stop_sent" if stage == "stop" else "warning_sent"
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO alert_events
                    (ad_id, stage, state, matched_rule_codes, metrics_json,
                     open_state_token, scan_id)
                VALUES
                    (:aid, :stage, :state,
                     CAST(:mrc AS JSONB), CAST(:m AS JSONB), :tok, :sid)
                """
            ),
            {
                "aid": ad_id,
                "stage": stage,
                "state": state,
                "mrc": json.dumps(rule_codes),
                "m": json.dumps(metrics),
                "tok": token,
                "sid": scan_id,
            },
        )


# Сценарий: один WARNING → один sendMessage с правильным thread + кнопками
@pytest.mark.asyncio
async def test_dispatch_warning_sends_one_message(
    pg_engine, tg_respx, seeded_telegram_config, offer_and_ad
) -> None:
    token = uuid.uuid4()
    await _insert_alert(
        pg_engine,
        ad_id=offer_and_ad["ad_id"],
        stage="warning",
        scan_id=100,
        token=token,
        rule_codes=["cpc_warning"],
        metrics={"spend": "5.0", "cpc": "0.20", "deposits": 0},
    )

    async with httpx.AsyncClient() as http:
        client = TelegramBotClient(bot_token="FAKE", http_client=http)
        counters = await dispatch_pending_alerts(pg_engine, client=client, scan_id=100)

    assert counters["sent"] == 1
    assert counters["skipped_duplicates"] == 0
    assert len(tg_respx.sent_messages) == 1

    sent = tg_respx.sent_messages[0]
    # Волна 2: DM в личку recipient'у, не в супергруппу; thread_id всегда None
    assert int(sent["chat_id"]) == _DISPATCHER_TEST_RECIPIENT_CHAT_ID
    assert sent.get("message_thread_id") is None
    assert "ПРЕДУПРЕЖДЕНИЕ" in sent["text"]
    assert sent.get("parse_mode") == "HTML"
    # Кнопки
    assert "reply_markup" in sent
    btns = sent["reply_markup"]["inline_keyboard"][0]
    assert any(b["callback_data"].startswith("dis:") for b in btns)

    # message_ref сохранён в БД
    async with pg_engine.connect() as conn:
        n_refs = (
            await conn.execute(
                text("SELECT COUNT(*) FROM telegram_message_refs WHERE ad_id = :i"),
                {"i": offer_and_ad["ad_id"]},
            )
        ).scalar()
    assert n_refs == 1


# Сценарий: STOP → правильный thread_id (22 вместо 11)
@pytest.mark.asyncio
async def test_dispatch_stop_uses_stop_thread(
    pg_engine, tg_respx, seeded_telegram_config, offer_and_ad
) -> None:
    await _insert_alert(
        pg_engine,
        ad_id=offer_and_ad["ad_id"],
        stage="stop",
        scan_id=200,
        token=uuid.uuid4(),
        rule_codes=["spend_no_dep_stop"],
        metrics={"spend": "20.0", "deposits": 0},
    )

    async with httpx.AsyncClient() as http:
        client = TelegramBotClient(bot_token="FAKE", http_client=http)
        await dispatch_pending_alerts(pg_engine, client=client, scan_id=200)

    sent = tg_respx.sent_messages[0]
    # Волна 2: DM-модель — топики форума не используются, thread_id всегда None
    assert sent.get("message_thread_id") is None
    assert "СТОП" in sent["text"]


# Сценарий: повторный dispatch того же scan_id → 0 новых отправок (idempotent через ref)
@pytest.mark.asyncio
async def test_idempotent_skip_duplicates(
    pg_engine, tg_respx, seeded_telegram_config, offer_and_ad
) -> None:
    token = uuid.uuid4()
    await _insert_alert(
        pg_engine,
        ad_id=offer_and_ad["ad_id"],
        stage="warning",
        scan_id=300,
        token=token,
        rule_codes=["cpc"],
        metrics={"spend": "5.0"},
    )

    async with httpx.AsyncClient() as http:
        client = TelegramBotClient(bot_token="FAKE", http_client=http)
        # Первый прогон отправляет
        c1 = await dispatch_pending_alerts(pg_engine, client=client, scan_id=300)
        assert c1["sent"] == 1
        # Второй прогон с тем же scan_id — НЕ отправляет (ref уже есть)
        c2 = await dispatch_pending_alerts(pg_engine, client=client, scan_id=300)

    assert c2["sent"] == 0
    assert c2["skipped_duplicates"] == 1
    assert len(tg_respx.sent_messages) == 1


# Сценарий: 0 событий для scan_id → 0 отправок, 0 skip, 0 errors
@pytest.mark.asyncio
async def test_empty_scan_no_messages(pg_engine, tg_respx, offer_and_ad) -> None:
    async with httpx.AsyncClient() as http:
        client = TelegramBotClient(bot_token="FAKE", http_client=http)
        counters = await dispatch_pending_alerts(pg_engine, client=client, scan_id=999_999)
    assert counters["sent"] == 0
    assert tg_respx.sent_messages == []


# Сценарий: телеграм возвращает 400 (например plain HTTP error) → events помечаются errors
@pytest.mark.asyncio
async def test_telegram_api_error_counted(pg_engine, seeded_telegram_config, offer_and_ad) -> None:
    import respx
    from httpx import Response

    token = uuid.uuid4()
    await _insert_alert(
        pg_engine,
        ad_id=offer_and_ad["ad_id"],
        stage="warning",
        scan_id=400,
        token=token,
        rule_codes=["cpc"],
        metrics={"spend": "5.0"},
    )

    with respx.mock(assert_all_called=False) as mock:
        mock.post(url__regex=r"https://api\.telegram\.org/bot[^/]+/sendMessage").mock(
            return_value=Response(400, json={"ok": False, "description": "chat not found"})
        )

        async with httpx.AsyncClient() as http:
            client = TelegramBotClient(bot_token="FAKE", http_client=http)
            counters = await dispatch_pending_alerts(pg_engine, client=client, scan_id=400)

    assert counters["sent"] == 0
    assert counters["errors"] == 1

    # message_ref НЕ создан — иначе ретрай не сработает
    async with pg_engine.connect() as conn:
        n_refs = (
            await conn.execute(
                text("SELECT COUNT(*) FROM telegram_message_refs WHERE ad_id = :i"),
                {"i": offer_and_ad["ad_id"]},
            )
        ).scalar()
    assert n_refs == 0

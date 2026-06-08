# -*- coding: utf-8 -*-
"""E2E cross-cutting сценарий: observer pipeline → alert_dispatcher → TG.

Сшивка двух подсистем:
1. `core/observer/pipeline.process_scan_rows` создаёт alert_events записи
   через FSM (а не вручную INSERT).
2. `core/telegram/alert_dispatcher.dispatch_pending_alerts` отправляет их
   в Telegram через respx-моки и пишет message_refs.

Дополнительно: при двух последовательных сканах одного STOP-инцидента
второй scan не должен дублировать ни alert_events, ни telegram_message_refs
— гарантия «алерт уходит ровно один раз» end-to-end через две подсистемы.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from core.observer.pipeline import process_scan_rows
from core.scanner.models import ScannedAdRow
from core.telegram.alert_dispatcher import dispatch_pending_alerts
from core.telegram.client import TelegramBotClient


@pytest_asyncio.fixture
async def clean_alert_e2e(pg_engine):
    """Чистит таблицы pipeline + message_refs до/после теста."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            for t in (
                "telegram_message_refs",
                "task_queue",
                "alert_events",
                "ad_metrics",
                "ad_alert_state",
                "fb_ads",
                "fb_adsets",
                "fb_campaigns",
                "offer_rules",
                "offers",
            ):
                await conn.execute(text(f"DELETE FROM {t}"))

    await _truncate()
    yield
    await _truncate()


@pytest_asyncio.fixture
async def offer_alert_e2e(pg_engine, clean_alert_e2e):
    """Оффер с CPA=10 для fast-stop."""
    offer_id = uuid.uuid4()
    code = f"ALR{uuid.uuid4().hex[:4].upper()}"
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name, is_active) VALUES (:i, :c, :n, TRUE)"),
            {"i": offer_id, "c": code, "n": f"Alert E2E {code}"},
        )
        await conn.execute(
            text("INSERT INTO offer_rules (offer_id, cpa_threshold) VALUES (:o, :cpa)"),
            {"o": offer_id, "cpa": Decimal("10.00")},
        )
    return {"offer_id": offer_id, "code": code}


def _stop_row(*, code: str, fb_ad_id: str) -> ScannedAdRow:
    """ScannedAdRow с метриками для FSM-STOP (spend без deposits)."""
    return ScannedAdRow(
        fb_ad_id=fb_ad_id,
        campaign_name=f"{code} | KE | promo",
        adset_name="ADS_E2E",
        ad_name="AD_e2e_alert",
        delivery_status="ACTIVE",
        spend=Decimal("25.00"),
        leads=0,
        registrations=0,
        deposits=0,
        cpc=Decimal("0.10"),
        ctr=Decimal("2.5"),
    )


# E2E: scan → FSM эмитит stop alert_event → dispatcher шлёт TG → ref в БД
@pytest.mark.asyncio
async def test_scan_emits_alert_dispatcher_delivers_once(
    pg_engine,
    offer_alert_e2e,
    seeded_telegram_config,
    tg_respx,
) -> None:
    fb_ad_id = f"230055{uuid.uuid4().hex[:6]}"
    row = _stop_row(code=offer_alert_e2e["code"], fb_ad_id=fb_ad_id)

    # Шаг 1: реальный observer pipeline создаёт alert_events через FSM
    cycle_result = await process_scan_rows(pg_engine, rows=[row], scan_id=500)
    assert cycle_result.alerts_stop == 1

    # Шаг 2: dispatcher шлёт его в TG через respx
    async with httpx.AsyncClient() as http:
        client = TelegramBotClient(bot_token="FAKE", http_client=http)
        counters = await dispatch_pending_alerts(pg_engine, client=client, scan_id=500)

    assert counters["sent"] == 1
    assert counters["skipped_duplicates"] == 0
    assert counters["errors"] == 0

    # Шаг 3: TG получил один payload с правильным thread_id (stop) и кнопками
    assert len(tg_respx.sent_messages) == 1
    sent = tg_respx.sent_messages[0]
    assert sent["chat_id"] == "-1001234567890"
    assert sent.get("message_thread_id") == 22  # forum_stop_thread_id из fixture
    assert "СТОП" in sent["text"]
    keyboard = sent["reply_markup"]["inline_keyboard"][0]
    assert any(b["callback_data"].startswith("dis:") for b in keyboard)

    # Шаг 4: message_ref сохранён → следующий dispatch не повторит
    async with pg_engine.connect() as conn:
        ref_count = (
            await conn.execute(
                text("SELECT COUNT(*) FROM telegram_message_refs WHERE incident_key IS NOT NULL")
            )
        ).scalar()
    assert ref_count == 1


# E2E: повторный scan того же STOP-row → ни одного нового alert_event,
# ни одного нового sendMessage в TG (двойная idempotency: FSM + ref-dedup).
@pytest.mark.asyncio
async def test_two_scans_one_dispatch_no_duplicate_tg_message(
    pg_engine,
    offer_alert_e2e,
    seeded_telegram_config,
    tg_respx,
) -> None:
    fb_ad_id = f"230056{uuid.uuid4().hex[:6]}"
    row = _stop_row(code=offer_alert_e2e["code"], fb_ad_id=fb_ad_id)

    # Первый scan — создаёт alert_event и шлёт в TG
    await process_scan_rows(pg_engine, rows=[row], scan_id=600)
    async with httpx.AsyncClient() as http:
        client = TelegramBotClient(bot_token="FAKE", http_client=http)
        c1 = await dispatch_pending_alerts(pg_engine, client=client, scan_id=600)
    assert c1["sent"] == 1

    # Второй scan того же ad'а — FSM не эмитит (stop_sent → stop_sent без emit)
    await process_scan_rows(pg_engine, rows=[row], scan_id=601)
    async with httpx.AsyncClient() as http:
        client = TelegramBotClient(bot_token="FAKE", http_client=http)
        c2 = await dispatch_pending_alerts(pg_engine, client=client, scan_id=601)
    # В этом scan'е alert_events для scan_id=601 нет → 0 sent, 0 skipped
    assert c2["sent"] == 0
    assert c2["skipped_duplicates"] == 0

    # Всего одно TG-сообщение за все скан-циклы
    assert len(tg_respx.sent_messages) == 1

    # alert_events за всё про всё — одна запись (первый scan)
    async with pg_engine.connect() as conn:
        n = (await conn.execute(text("SELECT COUNT(*) FROM alert_events"))).scalar()
    assert n == 1


# E2E: первый dispatch успешен, повторный dispatch того же scan_id → skip через ref.
# Защита от двойного запуска dispatcher'а (например через две параллельные observer-инстанции).
@pytest.mark.asyncio
async def test_double_dispatch_same_scan_id_skipped_via_message_ref(
    pg_engine,
    offer_alert_e2e,
    seeded_telegram_config,
    tg_respx,
) -> None:
    fb_ad_id = f"230057{uuid.uuid4().hex[:6]}"
    row = _stop_row(code=offer_alert_e2e["code"], fb_ad_id=fb_ad_id)

    await process_scan_rows(pg_engine, rows=[row], scan_id=700)

    async with httpx.AsyncClient() as http:
        client = TelegramBotClient(bot_token="FAKE", http_client=http)
        c1 = await dispatch_pending_alerts(pg_engine, client=client, scan_id=700)
        # Второй раз тот же scan_id — все события уже имеют message_ref → skip
        c2 = await dispatch_pending_alerts(pg_engine, client=client, scan_id=700)

    assert c1["sent"] == 1
    assert c2["sent"] == 0
    assert c2["skipped_duplicates"] == 1
    assert len(tg_respx.sent_messages) == 1

# -*- coding: utf-8 -*-
"""Интеграционные тесты мониторинга отказа канала авто-стопа в health_watchdog.

Инцидент 2026-06-19: канал исполнения авто-стопа (Marketing API через Vision fetch)
лёг, задачи pause_ad/bot_auto_stop зависли в retrying, объявления остались stop_sent
при delivery_status=ACTIVE и продолжали тратить — без единого сигнала.

Проверяем сквозную логику двух триггеров поверх реальной схемы Postgres:
- query_stuck_pause_tasks — задачи pause_ad/bot_auto_stop незавершены дольше N минут;
- query_desynced_stop_ads — рассинхрон stop_sent ↔ delivery_status=ACTIVE дольше M минут;
- check_autostop_channel — единый CRITICAL-алерт с дедупом (раз в час, пока не починят).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text

from apps.health_watchdog.main import (
    AUTOSTOP_DEDUP_KEY,
    check_autostop_channel,
    query_desynced_stop_ads,
    query_stuck_pause_tasks,
    run_one_check,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class FakeTGClient:
    """Минимальный стаб TelegramBotClient: фиксирует send_message вызовы."""

    sent: list[dict] = field(default_factory=list)

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        message_thread_id: int | None = None,
        reply_markup: dict | None = None,
        parse_mode: str | None = "HTML",
    ) -> dict:
        self.sent.append({"chat_id": chat_id, "text": text, "thread_id": message_thread_id})
        return {"message_id": len(self.sent)}

    async def close(self) -> None:
        pass


@pytest_asyncio.fixture
async def clean_autostop_tables(pg_engine):
    """Чистит таблицы, которые трогают триггеры канала авто-стопа."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            for t in ("task_queue", "ad_alert_state", "fb_ads", "fb_adsets", "fb_campaigns"):
                await conn.execute(text(f"DELETE FROM {t}"))

    await _truncate()
    yield
    await _truncate()


async def _insert_pause_task(
    pg_engine,
    *,
    fb_ad_id: str,
    status: str,
    requested_by: str = "bot_auto_stop",
    mutation_kind: str = "pause_ad",
    age_minutes: int,
    attempt_count: int = 16,
    last_error: str | None = "Failed to fetch",
) -> None:
    """Вставляет task_queue с заданным возрастом created_at (минуты назад)."""
    payload = json.dumps(
        {"mutation_kind": mutation_kind, "target_id": fb_ad_id, "params": {}, "ad_account_id": None}
    )
    created_at = _utcnow() - timedelta(minutes=age_minutes)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO task_queue
                    (task_type, status, idempotency_key, payload, requested_by,
                     attempt_count, max_attempts, last_error, created_at, updated_at)
                VALUES
                    ('meta_api_mutation', :st, :ik, CAST(:pl AS jsonb), :rb,
                     :ac, 72, :err, :ct, :ct)
                """
            ),
            {
                "st": status,
                "ik": f"auto:pause_ad:{fb_ad_id}:{uuid.uuid4()}",
                "pl": payload,
                "rb": requested_by,
                "ac": attempt_count,
                "err": last_error,
                "ct": created_at,
            },
        )


async def _insert_ad_with_state(
    pg_engine,
    *,
    fb_ad_id: str,
    alert_state: str,
    delivery_status: str | None,
    transition_age_minutes: int,
) -> uuid.UUID:
    """Создаёт fb_campaigns→fb_adsets→fb_ads→ad_alert_state с заданными полями."""
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]
    last_transition = _utcnow() - timedelta(minutes=transition_age_minutes)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO fb_campaigns (id, campaign_name) VALUES (:i, :n)"),
            {"i": campaign_id, "n": f"CMP_{suffix}"},
        )
        await conn.execute(
            text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
            {"i": adset_id, "c": campaign_id, "n": f"ADSET_{suffix}"},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name, delivery_status) "
                "VALUES (:i, :a, :f, :n, :ds)"
            ),
            {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"AD_{suffix}", "ds": delivery_status},
        )
        await conn.execute(
            text(
                "INSERT INTO ad_alert_state (ad_id, alert_state, current_stage, last_transition_at) "
                "VALUES (:aid, :st, 'stop', :ts)"
            ),
            {"aid": ad_id, "st": alert_state, "ts": last_transition},
        )
    return ad_id


# ====================== Триггер 3: застрявшие задачи pause_ad ======================


# Сценарий: задача авто-стопа зависла дольше порога → попадает в выборку
@pytest.mark.asyncio
async def test_stuck_pause_task_old_is_detected(pg_engine, clean_autostop_tables):
    await _insert_pause_task(pg_engine, fb_ad_id="23001", status="retrying", age_minutes=42)

    rows = await query_stuck_pause_tasks(pg_engine, minutes=15)

    assert len(rows) == 1
    assert rows[0].target_id == "23001"
    assert rows[0].attempt_count == 16
    assert rows[0].last_error == "Failed to fetch"
    assert rows[0].age_minutes >= 42  # возраст с момента created_at


# Сценарий: свежая задача (моложе порога) — НЕ алертим, это нормальный retry-цикл
@pytest.mark.asyncio
async def test_stuck_pause_task_fresh_is_ignored(pg_engine, clean_autostop_tables):
    await _insert_pause_task(pg_engine, fb_ad_id="23002", status="retrying", age_minutes=5)

    rows = await query_stuck_pause_tasks(pg_engine, minutes=15)

    assert rows == []


# Сценарий: успешно выполненная задача авто-стопа — не считается застрявшей
@pytest.mark.asyncio
async def test_stuck_pause_task_succeeded_is_ignored(pg_engine, clean_autostop_tables):
    await _insert_pause_task(pg_engine, fb_ad_id="23003", status="succeeded", age_minutes=60)

    rows = await query_stuck_pause_tasks(pg_engine, minutes=15)

    assert rows == []


# Сценарий: ручная пауза (requested_by != bot_auto_stop) не относится к авто-стопу
@pytest.mark.asyncio
async def test_stuck_pause_task_manual_is_ignored(pg_engine, clean_autostop_tables):
    await _insert_pause_task(
        pg_engine, fb_ad_id="23004", status="retrying", age_minutes=60, requested_by="user:42"
    )

    rows = await query_stuck_pause_tasks(pg_engine, minutes=15)

    assert rows == []


# Сценарий: другая мутация (не pause_ad) от bot_auto_stop не относится к каналу авто-стопа
@pytest.mark.asyncio
async def test_stuck_other_mutation_is_ignored(pg_engine, clean_autostop_tables):
    await _insert_pause_task(
        pg_engine, fb_ad_id="23005", status="retrying", age_minutes=60, mutation_kind="activate_ad"
    )

    rows = await query_stuck_pause_tasks(pg_engine, minutes=15)

    assert rows == []


# Сценарий: задача застряла в running (воркер завис на fetch-таймауте) тоже ловится
@pytest.mark.asyncio
async def test_stuck_pause_task_running_is_detected(pg_engine, clean_autostop_tables):
    await _insert_pause_task(pg_engine, fb_ad_id="23006", status="running", age_minutes=30)

    rows = await query_stuck_pause_tasks(pg_engine, minutes=15)

    assert len(rows) == 1
    assert rows[0].target_id == "23006"


# ====================== Триггер 2: рассинхрон stop_sent ↔ ACTIVE ======================


# Сценарий: stop_sent + delivery_status=ACTIVE дольше порога → рассинхрон (money-симптом)
@pytest.mark.asyncio
async def test_desync_stop_sent_active_is_detected(pg_engine, clean_autostop_tables):
    await _insert_ad_with_state(
        pg_engine,
        fb_ad_id="55001",
        alert_state="stop_sent",
        delivery_status="ACTIVE",
        transition_age_minutes=30,
    )

    rows = await query_desynced_stop_ads(pg_engine, minutes=15)

    assert len(rows) == 1
    assert rows[0].fb_ad_id == "55001"
    assert rows[0].age_minutes >= 30


# Сценарий: stop_sent, но delivery_status=Paused — авто-стоп довёл объявление до OFF, всё ок
@pytest.mark.asyncio
async def test_desync_stop_sent_paused_is_ok(pg_engine, clean_autostop_tables):
    await _insert_ad_with_state(
        pg_engine,
        fb_ad_id="55002",
        alert_state="stop_sent",
        delivery_status="Paused",
        transition_age_minutes=30,
    )

    rows = await query_desynced_stop_ads(pg_engine, minutes=15)

    assert rows == []


# Сценарий: ACTIVE, но FSM=normal — обычное живое объявление, не рассинхрон
@pytest.mark.asyncio
async def test_desync_normal_active_is_ok(pg_engine, clean_autostop_tables):
    await _insert_ad_with_state(
        pg_engine,
        fb_ad_id="55003",
        alert_state="normal",
        delivery_status="ACTIVE",
        transition_age_minutes=30,
    )

    rows = await query_desynced_stop_ads(pg_engine, minutes=15)

    assert rows == []


# Сценарий: рассинхрон свежий (моложе порога) — даём каналу время довести стоп, не паникуем
@pytest.mark.asyncio
async def test_desync_fresh_is_ignored(pg_engine, clean_autostop_tables):
    await _insert_ad_with_state(
        pg_engine,
        fb_ad_id="55004",
        alert_state="stop_sent",
        delivery_status="ACTIVE",
        transition_age_minutes=5,
    )

    rows = await query_desynced_stop_ads(pg_engine, minutes=15)

    assert rows == []


# Сценарий: delivery_status в другом регистре ('active') всё равно считается ACTIVE
@pytest.mark.asyncio
async def test_desync_active_case_insensitive(pg_engine, clean_autostop_tables):
    await _insert_ad_with_state(
        pg_engine,
        fb_ad_id="55005",
        alert_state="stop_sent",
        delivery_status="active",
        transition_age_minutes=30,
    )

    rows = await query_desynced_stop_ads(pg_engine, minutes=15)

    assert len(rows) == 1
    assert rows[0].fb_ad_id == "55005"


# Сценарий: delivery_status=NULL (ещё не сканировали) — не знаем что крутится, не алертим ложно
@pytest.mark.asyncio
async def test_desync_null_delivery_is_ignored(pg_engine, clean_autostop_tables):
    await _insert_ad_with_state(
        pg_engine,
        fb_ad_id="55006",
        alert_state="stop_sent",
        delivery_status=None,
        transition_age_minutes=30,
    )

    rows = await query_desynced_stop_ads(pg_engine, minutes=15)

    assert rows == []


# ====================== Оркестрация check_autostop_channel ======================


# Сценарий: отказ канала → ровно один CRITICAL-алерт в ops-топик, дедуп держит повтор
@pytest.mark.asyncio
async def test_check_autostop_channel_alerts_once(
    pg_engine, fake_redis_client, clean_autostop_tables
):
    await _insert_pause_task(pg_engine, fb_ad_id="23010", status="retrying", age_minutes=40)
    await _insert_ad_with_state(
        pg_engine,
        fb_ad_id="55010",
        alert_state="stop_sent",
        delivery_status="ACTIVE",
        transition_age_minutes=40,
    )
    tg = FakeTGClient()

    first = await check_autostop_channel(
        pg_engine,
        fake_redis_client,
        tg_client=tg,
        chat_id="100",
        thread_id=7,
        stuck_after_minutes=15,
        desync_after_minutes=15,
    )
    second = await check_autostop_channel(
        pg_engine,
        fake_redis_client,
        tg_client=tg,
        chat_id="100",
        thread_id=7,
        stuck_after_minutes=15,
        desync_after_minutes=15,
    )

    assert first is True
    assert second is False  # дедуп: повторно не шлём
    assert len(tg.sent) == 1
    msg = tg.sent[0]
    assert "23010" in msg["text"]  # застрявшая задача
    assert "55010" in msg["text"]  # рассинхрон
    assert msg["thread_id"] == 7  # ops-топик
    assert await fake_redis_client.get(AUTOSTOP_DEDUP_KEY) == "1"


# Сценарий: канал здоров (нет ни застрявших задач, ни рассинхрона) → молчим
@pytest.mark.asyncio
async def test_check_autostop_channel_healthy_no_alert(
    pg_engine, fake_redis_client, clean_autostop_tables
):
    # Свежая задача и корректно отключённое объявление — оба не должны тревожить
    await _insert_pause_task(pg_engine, fb_ad_id="23011", status="retrying", age_minutes=3)
    await _insert_ad_with_state(
        pg_engine,
        fb_ad_id="55011",
        alert_state="stop_sent",
        delivery_status="Paused",
        transition_age_minutes=40,
    )
    tg = FakeTGClient()

    sent = await check_autostop_channel(
        pg_engine,
        fake_redis_client,
        tg_client=tg,
        chat_id="100",
        thread_id=7,
        stuck_after_minutes=15,
        desync_after_minutes=15,
    )

    assert sent is False
    assert tg.sent == []
    assert await fake_redis_client.get(AUTOSTOP_DEDUP_KEY) is None


# Сценарий: run_one_check с engine включает проверку канала авто-стопа в общий прогон
@pytest.mark.asyncio
async def test_run_one_check_includes_autostop(pg_engine, fake_redis_client, clean_autostop_tables):
    # heartbeat'ы живые и observer:runtime свежий — единственный источник алерта это автостоп
    await fake_redis_client.set("worker:heartbeat:observer", "alive", ex=60)
    await fake_redis_client.set(
        "observer:runtime",
        json.dumps({"worker_status": "scanning", "updated_at": _utcnow().isoformat()}),
        ex=60,
    )
    await _insert_pause_task(pg_engine, fb_ad_id="23099", status="retrying", age_minutes=40)
    tg = FakeTGClient()

    await run_one_check(
        fake_redis_client,
        expected_workers=["observer"],
        tg_client=tg,
        chat_id="100",
        thread_id=7,
        engine=pg_engine,
    )

    assert len(tg.sent) == 1
    assert "23099" in tg.sent[0]["text"]
    assert tg.sent[0]["thread_id"] == 7


# Сценарий: run_one_check без engine не падает и не трогает канал авто-стопа (обратная совместимость)
@pytest.mark.asyncio
async def test_run_one_check_without_engine_skips_autostop(
    pg_engine, fake_redis_client, clean_autostop_tables
):
    await fake_redis_client.set("worker:heartbeat:observer", "alive", ex=60)
    await fake_redis_client.set(
        "observer:runtime",
        json.dumps({"worker_status": "scanning", "updated_at": _utcnow().isoformat()}),
        ex=60,
    )
    await _insert_pause_task(pg_engine, fb_ad_id="23098", status="retrying", age_minutes=40)
    tg = FakeTGClient()

    # engine не передан → autostop-проверка пропускается, исключений нет
    await run_one_check(
        fake_redis_client,
        expected_workers=["observer"],
        tg_client=tg,
        chat_id="100",
        thread_id=7,
    )

    assert tg.sent == []
    assert await fake_redis_client.get(AUTOSTOP_DEDUP_KEY) is None

# -*- coding: utf-8 -*-
"""Тесты publish в Redis pubsub-каналы из воркеров FB Stop Bot.

Покрывает три publish-точки:
1. alert_dispatcher  → fb_agent:alert:created  (после отправки алерта в TG)
2. meta_api_worker   → fb_agent:task:changed   (после mark_succeeded pause_ad и др.)
3. health_watchdog   → fb_agent:health:updated (после цикла проверки heartbeat'ов)

Все тесты используют fakeredis — реальный Redis не нужен.
Тест E2E: publish в канал → WS-хендлер форвардит клиенту.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid

import fakeredis.aioredis
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from core.telegram.alert_dispatcher import dispatch_pending_alerts
from core.telegram.client import TelegramBotClient

# ====================== fixtures ======================


@pytest_asyncio.fixture
async def fake_redis():
    """Изолированный fakeredis для каждого теста."""
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


# Волна 2: dispatch по recipients, не по config.chat_id. Scoped chat_id для этого файла.
_PUBSUB_RECIPIENT_CHAT_ID = 44332211


@pytest_asyncio.fixture
async def offer_and_ad_for_pubsub(pg_engine):
    """Создаёт иерархию offer→campaign→adset→ad для тестов dispatcher'а.

    Telegram config seed'ится отдельной фикстурой seeded_telegram_config из conftest.
    Волна 2: также сеем одного telegram_recipient для DM-рассылки.
    """
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
            {"i": offer_id, "c": f"PUB_{suffix}", "n": "Pubsub test offer"},
        )
        await conn.execute(
            text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
            {"i": campaign_id, "n": f"CMP_PUB_{suffix}", "o": offer_id},
        )
        await conn.execute(
            text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
            {"i": adset_id, "c": campaign_id, "n": f"ADSET_PUB_{suffix}"},
        )
        await conn.execute(
            text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
            {
                "i": ad_id,
                "a": adset_id,
                "f": f"23001{suffix}",
                "n": f"AD_PUB_{suffix}",
            },
        )
        # Волна 2: сеем одного recipient'а для DM-отправки
        await conn.execute(
            text(
                "INSERT INTO telegram_recipients "
                "(id, chat_id, telegram_user_id, role) "
                "VALUES (gen_random_uuid(), :c, :c, 'recipient') "
                "ON CONFLICT (chat_id, telegram_user_id) DO NOTHING"
            ),
            {"c": _PUBSUB_RECIPIENT_CHAT_ID},
        )

    yield {
        "offer_id": offer_id,
        "campaign_id": campaign_id,
        "adset_id": adset_id,
        "ad_id": ad_id,
        "fb_ad_id": f"23001{suffix}",
        "suffix": suffix,
    }

    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_message_refs WHERE ad_id = :i"), {"i": ad_id})
        await conn.execute(text("DELETE FROM alert_events WHERE ad_id = :i"), {"i": ad_id})
        await conn.execute(text("DELETE FROM offers WHERE id = :i"), {"i": offer_id})
        await conn.execute(
            text("DELETE FROM telegram_recipients WHERE chat_id = :c"),
            {"c": _PUBSUB_RECIPIENT_CHAT_ID},
        )


async def _insert_alert_pubsub(
    engine,
    *,
    ad_id,
    stage: str,
    scan_id: int,
    token: uuid.UUID,
    rule_codes: list[str],
) -> None:
    """Вставляет тестовый alert_event в БД."""
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
                "m": json.dumps({"spend": "10.0"}),
                "tok": token,
                "sid": scan_id,
            },
        )


# ====================== тест 1: alert_dispatcher публикует в fb_agent:alert:created ======================


# Dispatch алерта через TG → должно появиться сообщение в fb_agent:alert:created
@pytest.mark.asyncio
async def test_alert_dispatcher_publishes_alert_created(
    pg_engine,
    tg_respx,
    seeded_telegram_config,
    offer_and_ad_for_pubsub,
    fake_redis,
) -> None:
    """Проверяем: после успешной TG-отправки dispatcher публикует событие в Redis-канал."""
    token = uuid.uuid4()
    scan_id = 5001
    await _insert_alert_pubsub(
        pg_engine,
        ad_id=offer_and_ad_for_pubsub["ad_id"],
        stage="stop",
        scan_id=scan_id,
        token=token,
        rule_codes=["spend_no_dep_stop"],
    )

    # Подписываемся на канал через fakeredis
    pubsub = fake_redis.pubsub()
    await pubsub.subscribe("fb_agent:alert:created")
    # Пропускаем subscribe-подтверждение
    await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)

    async with httpx.AsyncClient() as http:
        client = TelegramBotClient(bot_token="FAKE_BOT", http_client=http)
        counters = await dispatch_pending_alerts(
            pg_engine, client=client, scan_id=scan_id, redis_client=fake_redis
        )

    assert counters["sent"] == 1, "алерт должен быть отправлен в TG"

    # Ждём сообщение в канале (несколько попыток с небольшой задержкой)
    received = None
    for _ in range(20):
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
        if msg and msg["type"] == "message":
            received = msg
            break
        await asyncio.sleep(0.05)

    await pubsub.unsubscribe("fb_agent:alert:created")
    await pubsub.aclose()

    assert received is not None, "ожидали сообщение в fb_agent:alert:created"
    data = json.loads(received["data"])
    assert data["fb_ad_id"] == offer_and_ad_for_pubsub["fb_ad_id"]
    assert data["stage"] == "stop"
    assert "spend_no_dep_stop" in data["matched_rule_codes"]
    assert "alert_event_id" in data
    assert "timestamp" in data


# Дублированный dispatch (idempotency) — publish должен быть ровно один раз
@pytest.mark.asyncio
async def test_alert_dispatcher_no_publish_on_dedup_skip(
    pg_engine,
    tg_respx,
    seeded_telegram_config,
    offer_and_ad_for_pubsub,
    fake_redis,
) -> None:
    """При skipped_duplicates (pre-claim уже занят) publish не должен происходить."""
    token = uuid.uuid4()
    scan_id = 5002
    await _insert_alert_pubsub(
        pg_engine,
        ad_id=offer_and_ad_for_pubsub["ad_id"],
        stage="warning",
        scan_id=scan_id,
        token=token,
        rule_codes=["cpc_warning"],
    )

    async with httpx.AsyncClient() as http:
        client = TelegramBotClient(bot_token="FAKE_BOT", http_client=http)
        # Первый вызов — реальная отправка
        c1 = await dispatch_pending_alerts(
            pg_engine, client=client, scan_id=scan_id, redis_client=fake_redis
        )
        assert c1["sent"] == 1

    # Считаем опубликованные сообщения после первого вызова
    pubsub = fake_redis.pubsub()
    await pubsub.subscribe("fb_agent:alert:created")
    await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)

    async with httpx.AsyncClient() as http:
        client = TelegramBotClient(bot_token="FAKE_BOT", http_client=http)
        # Второй вызов — тот же scan_id → skipped_duplicates=1, sent=0
        c2 = await dispatch_pending_alerts(
            pg_engine, client=client, scan_id=scan_id, redis_client=fake_redis
        )
    assert c2["sent"] == 0
    assert c2["skipped_duplicates"] == 1

    # Никаких сообщений в канале (дедуп сработал)
    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
    await pubsub.unsubscribe("fb_agent:alert:created")
    await pubsub.aclose()

    assert msg is None, "при ddup-skip publish не должен происходить"


# ====================== тест 2: meta_api_worker публикует в fb_agent:task:changed ======================


# process_one_task (pause_ad) → mark_succeeded → publish task:changed со статусом succeeded
@pytest.mark.asyncio
async def test_meta_api_worker_publishes_task_changed_on_success(
    pg_engine,
    fake_redis,
    monkeypatch,
) -> None:
    """Проверяем: process_one_task публикует task:changed при успешном pause_ad."""
    from unittest.mock import AsyncMock

    from apps.meta_api_worker.main import process_one_task
    from core.meta_api.queue import claim_pending_task, create_mutation_task
    from core.meta_api.schemas import MetaMutationPayload

    # Создаём meta_api_mutation задачу со статусом pending
    fb_ad_id = uuid.uuid4().hex
    payload = MetaMutationPayload(
        mutation_kind="pause_ad",
        target_id=fb_ad_id,
        params={"reason": "pubsub test"},
        ad_account_id="act_42",
    )
    task_id = await create_mutation_task(
        pg_engine,
        payload=payload,
        requested_by="test",
        status="pending",
    )
    assert task_id is not None

    # Подписываемся на канал до прогона задачи
    pubsub = fake_redis.pubsub()
    await pubsub.subscribe("fb_agent:task:changed")
    await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)

    # Мокаем dispatch_mutation → успешный результат без реального gRPC
    fake_result = {"success": True, "modified_ids": [fb_ad_id]}

    async def _fake_dispatch(client, p):
        return fake_result

    import apps.meta_api_worker.main as worker_main

    monkeypatch.setattr(worker_main, "dispatch_mutation", _fake_dispatch)
    # Отключаем owner-scoping и scanning-gate чтобы не требовать БД-настроек
    monkeypatch.setattr(worker_main, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        worker_main,
        "check_mutation_ownership",
        AsyncMock(return_value=type("R", (), {"allowed": True})()),
    )
    monkeypatch.setattr(worker_main, "load_owner_tag", AsyncMock(return_value=None))
    # FSM-sync — best-effort, мокаем чтобы не тянуть FSM
    monkeypatch.setattr(worker_main, "sync_fsm_after_mutation", AsyncMock())

    claim = await claim_pending_task(pg_engine)
    assert not claim.queue_empty, "задача должна быть в очереди"
    assert claim.task is not None
    assert claim.task.id == task_id

    fake_client = AsyncMock()
    await process_one_task(pg_engine, claim.task, client=fake_client, redis_client=fake_redis)

    # Ожидаем сообщение в канале
    received = None
    for _ in range(20):
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
        if msg and msg["type"] == "message":
            received = msg
            break
        await asyncio.sleep(0.05)

    await pubsub.unsubscribe("fb_agent:task:changed")
    await pubsub.aclose()

    # Cleanup
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM task_queue WHERE id = :i"), {"i": task_id})

    assert received is not None, "ожидали сообщение в fb_agent:task:changed"
    data = json.loads(received["data"])
    assert data["task_id"] == task_id
    assert data["task_type"] == "meta_api_mutation"
    assert data["status"] == "succeeded"
    assert "timestamp" in data


# Без redis_client (None) process_one_task не падает (best-effort publish)
@pytest.mark.asyncio
async def test_meta_api_worker_no_redis_does_not_crash(
    pg_engine,
    monkeypatch,
) -> None:
    """Воркер не падает если redis_client=None (best-effort publish)."""
    from unittest.mock import AsyncMock

    from apps.meta_api_worker.main import process_one_task
    from core.meta_api.queue import claim_pending_task, create_mutation_task
    from core.meta_api.schemas import MetaMutationPayload

    fb_ad_id = uuid.uuid4().hex
    payload = MetaMutationPayload(
        mutation_kind="pause_ad",
        target_id=fb_ad_id,
        params={"reason": "no-redis test"},
        ad_account_id="act_42",
    )
    task_id = await create_mutation_task(
        pg_engine,
        payload=payload,
        requested_by="test",
        status="pending",
    )
    assert task_id is not None

    fake_result = {"success": True, "modified_ids": [fb_ad_id]}

    async def _fake_dispatch(client, p):
        return fake_result

    import apps.meta_api_worker.main as worker_main

    monkeypatch.setattr(worker_main, "dispatch_mutation", _fake_dispatch)
    monkeypatch.setattr(worker_main, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        worker_main,
        "check_mutation_ownership",
        AsyncMock(return_value=type("R", (), {"allowed": True})()),
    )
    monkeypatch.setattr(worker_main, "load_owner_tag", AsyncMock(return_value=None))
    monkeypatch.setattr(worker_main, "sync_fsm_after_mutation", AsyncMock())

    claim = await claim_pending_task(pg_engine)
    assert not claim.queue_empty
    assert claim.task is not None

    fake_client = AsyncMock()
    # redis_client=None — publish пропускается без исключения
    await process_one_task(pg_engine, claim.task, client=fake_client, redis_client=None)

    # Воркер отработал — задача succeeded
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row is not None
    assert row[0] == "succeeded"

    # Cleanup
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM task_queue WHERE id = :i"), {"i": task_id})


# ====================== тест 3: health_watchdog публикует в fb_agent:health:updated ======================


# run_one_check публикует fb_agent:health:updated после каждого прогона
@pytest.mark.asyncio
async def test_health_watchdog_publishes_health_updated(fake_redis) -> None:
    """run_one_check должен публиковать сводку health:updated в Redis-канал."""
    from apps.health_watchdog.main import run_one_check

    expected_workers = ["observer", "disable", "enable"]

    # Heartbeat только для "observer"
    await fake_redis.set("worker:heartbeat:observer", "alive", ex=60)

    pubsub = fake_redis.pubsub()
    await pubsub.subscribe("fb_agent:health:updated")
    await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)

    await run_one_check(
        fake_redis,
        expected_workers=expected_workers,
        tg_client=None,
        chat_id=None,
        thread_id=None,
    )

    # Ожидаем сообщение
    received = None
    for _ in range(20):
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
        if msg and msg["type"] == "message":
            received = msg
            break
        await asyncio.sleep(0.05)

    await pubsub.unsubscribe("fb_agent:health:updated")
    await pubsub.aclose()

    assert received is not None, "ожидали сообщение в fb_agent:health:updated"
    data = json.loads(received["data"])
    # disable и enable offline → DEGRADED
    assert data["overall"] == "DEGRADED"
    assert "disable" in data["offline"]
    assert "enable" in data["offline"]
    assert "observer" not in data["offline"]
    assert "timestamp" in data


# Все воркеры живы → overall=HEALTHY
@pytest.mark.asyncio
async def test_health_watchdog_healthy_when_all_alive(fake_redis) -> None:
    """Если все heartbeat'ы живы — публикуется HEALTHY."""
    from apps.health_watchdog.main import run_one_check

    expected_workers = ["observer", "disable"]
    for name in expected_workers:
        await fake_redis.set(f"worker:heartbeat:{name}", "alive", ex=60)

    pubsub = fake_redis.pubsub()
    await pubsub.subscribe("fb_agent:health:updated")
    await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)

    await run_one_check(
        fake_redis,
        expected_workers=expected_workers,
        tg_client=None,
        chat_id=None,
        thread_id=None,
    )

    received = None
    for _ in range(20):
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
        if msg and msg["type"] == "message":
            received = msg
            break
        await asyncio.sleep(0.05)

    await pubsub.unsubscribe("fb_agent:health:updated")
    await pubsub.aclose()

    assert received is not None
    data = json.loads(received["data"])
    assert data["overall"] == "HEALTHY"
    assert data["offline"] == []


# ====================== тест 4: best-effort — Redis падает → воркер не падает ======================


# Публикация в сломанный Redis не роняет dispatch_pending_alerts
@pytest.mark.asyncio
async def test_alert_dispatcher_survives_redis_publish_failure(
    pg_engine,
    tg_respx,
    seeded_telegram_config,
    offer_and_ad_for_pubsub,
) -> None:
    """Best-effort: если Redis недоступен при publish — dispatch всё равно успешен."""
    token = uuid.uuid4()
    scan_id = 5010
    await _insert_alert_pubsub(
        pg_engine,
        ad_id=offer_and_ad_for_pubsub["ad_id"],
        stage="warning",
        scan_id=scan_id,
        token=token,
        rule_codes=["cpc_warning"],
    )

    # Передаём объект с broken publish
    class BrokenRedis:
        async def publish(self, channel, message):
            raise ConnectionError("Redis недоступен (test)")

    async with httpx.AsyncClient() as http:
        client = TelegramBotClient(bot_token="FAKE_BOT", http_client=http)
        # Не должно падать, несмотря на сломанный Redis
        counters = await dispatch_pending_alerts(
            pg_engine, client=client, scan_id=scan_id, redis_client=BrokenRedis()
        )

    # TG-сообщение всё равно отправлено
    assert counters["sent"] == 1


# ====================== тест 5: E2E — publish → WS-клиент получает сообщение ======================


# Publish в fb_agent:task:changed → WS-хендлер форвардит клиенту с type=task_changed
def test_ws_receives_task_changed_e2e(monkeypatch) -> None:
    """E2E: publish task:changed в fakeredis → WS-клиент получает type=task_changed."""
    monkeypatch.setenv("WS_HEARTBEAT_SECONDS", "60")
    import importlib

    import apps.api.routers.ws as ws_mod

    importlib.reload(ws_mod)

    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    from apps.api.main import create_app

    app = create_app()
    app.state.redis = fake_redis
    app.state.ws_pubsub_redis = fake_redis

    from fastapi.testclient import TestClient

    client = TestClient(app)

    # Публикуем task:changed из отдельного потока с задержкой
    task_payload = {
        "task_id": 42,
        "task_type": "disable",
        "status": "succeeded",
        "timestamp": "2026-01-01T00:00:00+00:00",
    }

    def _publish():
        import asyncio

        loop = asyncio.new_event_loop()
        time.sleep(0.15)
        loop.run_until_complete(
            fake_redis.publish("fb_agent:task:changed", json.dumps(task_payload))
        )
        loop.close()

    t = threading.Thread(target=_publish, daemon=True)
    t.start()

    with client.websocket_connect("/ws/dashboard") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "task_changed"
        assert msg["payload"]["task_id"] == 42
        assert msg["payload"]["status"] == "succeeded"

    t.join(timeout=2)


# Publish в fb_agent:health:updated → WS-клиент получает type=health_updated
def test_ws_receives_health_updated_e2e(monkeypatch) -> None:
    """E2E: publish health:updated → WS форвардит клиенту с type=health_updated."""
    monkeypatch.setenv("WS_HEARTBEAT_SECONDS", "60")
    import importlib

    import apps.api.routers.ws as ws_mod

    importlib.reload(ws_mod)

    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    from apps.api.main import create_app

    app = create_app()
    app.state.redis = fake_redis
    app.state.ws_pubsub_redis = fake_redis

    from fastapi.testclient import TestClient

    client = TestClient(app)

    health_payload = {
        "overall": "DEGRADED",
        "offline": ["cleanup"],
        "timestamp": "2026-01-01T00:00:00+00:00",
    }

    def _publish():
        import asyncio

        loop = asyncio.new_event_loop()
        time.sleep(0.15)
        loop.run_until_complete(
            fake_redis.publish("fb_agent:health:updated", json.dumps(health_payload))
        )
        loop.close()

    t = threading.Thread(target=_publish, daemon=True)
    t.start()

    with client.websocket_connect("/ws/dashboard") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "health_updated"
        assert msg["payload"]["overall"] == "DEGRADED"
        assert "cleanup" in msg["payload"]["offline"]

    t.join(timeout=2)

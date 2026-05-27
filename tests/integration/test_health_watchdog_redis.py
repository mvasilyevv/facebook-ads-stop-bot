# -*- coding: utf-8 -*-
"""Интеграционные тесты health_watchdog: fakeredis + мок Telegram-клиента.

Проверяем сквозную логику ``run_one_check``:
- heartbeat есть → не алертим
- heartbeat истёк → алерт (один раз благодаря дедупу)
- observer:runtime устарел → алерт
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

from apps.health_watchdog.main import (
    check_observer_runtime,
    check_worker_heartbeats,
    run_one_check,
)


@dataclass
class FakeTGClient:
    """Минимальный стаб TelegramBotClient: фиксирует все send_message вызовы."""

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
        self.sent.append(
            {
                "chat_id": chat_id,
                "text": text,
                "thread_id": message_thread_id,
                "parse_mode": parse_mode,
            }
        )
        return {"message_id": len(self.sent)}

    async def close(self) -> None:
        pass


# Сценарий: heartbeat живой → ни одного TG-вызова
@pytest.mark.asyncio
async def test_alive_heartbeat_does_not_alert(fake_redis_client) -> None:
    await fake_redis_client.set("worker:heartbeat:disable", "alive", ex=60)
    tg = FakeTGClient()

    alerted = await check_worker_heartbeats(
        fake_redis_client,
        expected_workers=["disable"],
        tg_client=tg,
        chat_id="100",
        thread_id=None,
    )

    assert alerted == 0
    assert tg.sent == []


# Сценарий: heartbeat отсутствует → ровно один TG-алерт
@pytest.mark.asyncio
async def test_missing_heartbeat_triggers_alert(fake_redis_client) -> None:
    tg = FakeTGClient()

    alerted = await check_worker_heartbeats(
        fake_redis_client,
        expected_workers=["observer"],
        tg_client=tg,
        chat_id="100",
        thread_id=42,
    )

    assert alerted == 1
    assert len(tg.sent) == 1
    msg = tg.sent[0]
    assert "observer" in msg["text"]
    assert "не дышит" in msg["text"]
    assert msg["chat_id"] == "100"
    assert msg["thread_id"] == 42


# Сценарий: дедуп держит повторный алерт под капотом (Redis-ключ)
@pytest.mark.asyncio
async def test_alert_dedup_on_second_check(fake_redis_client) -> None:
    tg = FakeTGClient()

    first = await check_worker_heartbeats(
        fake_redis_client,
        expected_workers=["enable"],
        tg_client=tg,
        chat_id="100",
        thread_id=None,
    )
    second = await check_worker_heartbeats(
        fake_redis_client,
        expected_workers=["enable"],
        tg_client=tg,
        chat_id="100",
        thread_id=None,
    )

    assert first == 1
    assert second == 0
    assert len(tg.sent) == 1
    assert await fake_redis_client.get("health:alerted:enable") == "1"


# Сценарий: несколько ожидаемых воркеров — алертим по каждому отсутствующему
@pytest.mark.asyncio
async def test_multiple_workers_partial_missing(fake_redis_client) -> None:
    await fake_redis_client.set("worker:heartbeat:observer", "alive", ex=60)
    await fake_redis_client.set("worker:heartbeat:disable", "alive", ex=60)
    # enable и meta_api отсутствуют
    tg = FakeTGClient()

    alerted = await check_worker_heartbeats(
        fake_redis_client,
        expected_workers=["observer", "disable", "enable", "meta_api"],
        tg_client=tg,
        chat_id="100",
        thread_id=None,
    )

    assert alerted == 2
    texts = [m["text"] for m in tg.sent]
    assert any("enable" in t for t in texts)
    assert any("meta_api" in t for t in texts)


# Сценарий: observer:runtime отсутствует → отдельный алерт о stale
@pytest.mark.asyncio
async def test_observer_runtime_missing_alerts(fake_redis_client) -> None:
    tg = FakeTGClient()

    sent = await check_observer_runtime(
        fake_redis_client,
        tg_client=tg,
        chat_id="100",
        thread_id=None,
    )

    assert sent is True
    assert len(tg.sent) == 1
    assert "observer:runtime" in tg.sent[0]["text"]
    assert "missing" in tg.sent[0]["text"]


# Сценарий: observer:runtime свежий → молчим
@pytest.mark.asyncio
async def test_observer_runtime_fresh_no_alert(fake_redis_client) -> None:
    now = datetime.now(timezone.utc)
    payload = json.dumps({"worker_status": "scanning", "updated_at": now.isoformat()})
    await fake_redis_client.set("observer:runtime", payload, ex=60)
    tg = FakeTGClient()

    sent = await check_observer_runtime(
        fake_redis_client,
        tg_client=tg,
        chat_id="100",
        thread_id=None,
    )

    assert sent is False
    assert tg.sent == []


# Сценарий: observer:runtime со старым updated_at → алерт + дедуп
@pytest.mark.asyncio
async def test_observer_runtime_stale_alerts_once(fake_redis_client) -> None:
    stale_ts = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    payload = json.dumps({"worker_status": "idle", "updated_at": stale_ts})
    await fake_redis_client.set("observer:runtime", payload, ex=60)
    tg = FakeTGClient()

    first = await check_observer_runtime(
        fake_redis_client,
        tg_client=tg,
        chat_id="100",
        thread_id=None,
    )
    second = await check_observer_runtime(
        fake_redis_client,
        tg_client=tg,
        chat_id="100",
        thread_id=None,
    )

    assert first is True
    assert second is False
    assert len(tg.sent) == 1


# Сценарий: run_one_check проверяет и воркеров, и observer:runtime
@pytest.mark.asyncio
async def test_run_one_check_combines_both(fake_redis_client) -> None:
    await fake_redis_client.set("worker:heartbeat:observer", "alive", ex=60)
    tg = FakeTGClient()

    await run_one_check(
        fake_redis_client,
        expected_workers=["observer", "disable"],
        tg_client=tg,
        chat_id="100",
        thread_id=None,
    )

    # disable отсутствует + observer:runtime отсутствует → 2 алерта
    assert len(tg.sent) == 2
    texts = [m["text"] for m in tg.sent]
    assert any("disable" in t for t in texts)
    assert any("observer:runtime" in t for t in texts)


# Сценарий: без TG-клиента (config не настроен) проверки не падают
@pytest.mark.asyncio
async def test_no_tg_client_does_not_crash(fake_redis_client) -> None:
    await check_worker_heartbeats(
        fake_redis_client,
        expected_workers=["observer"],
        tg_client=None,
        chat_id=None,
        thread_id=None,
    )
    # Дедуп всё равно ставится, чтобы при появлении TG не задвоить алерт
    assert await fake_redis_client.get("health:alerted:observer") == "1"

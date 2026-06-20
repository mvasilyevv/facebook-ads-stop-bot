# -*- coding: utf-8 -*-
"""Интеграционные тесты health_watchdog: fakeredis + мок notify_recipients.

Проверяем сквозную логику ``run_one_check``:
- heartbeat есть → не алертим
- heartbeat истёк → алерт (один раз благодаря дедупу)
- observer:runtime устарел → алерт
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.health_watchdog.main import (
    check_observer_runtime,
    check_worker_heartbeats,
    run_one_check,
)


def _make_engine():
    return MagicMock()


# Сценарий: heartbeat живой → ни одного алерта
@pytest.mark.asyncio
async def test_alive_heartbeat_does_not_alert(fake_redis_client) -> None:
    await fake_redis_client.set("worker:heartbeat:disable", "alive", ex=60)
    engine = _make_engine()

    with patch("apps.health_watchdog.main.notify_recipients", AsyncMock(return_value=True)) as spy:
        alerted = await check_worker_heartbeats(
            fake_redis_client,
            expected_workers=["disable"],
            engine=engine,
        )

    assert alerted == 0
    spy.assert_not_awaited()


# Сценарий: heartbeat отсутствует → ровно один алерт через notify_recipients
@pytest.mark.asyncio
async def test_missing_heartbeat_triggers_alert(fake_redis_client) -> None:
    engine = _make_engine()

    with patch("apps.health_watchdog.main.notify_recipients", AsyncMock(return_value=True)) as spy:
        alerted = await check_worker_heartbeats(
            fake_redis_client,
            expected_workers=["observer"],
            engine=engine,
        )

    assert alerted == 1
    spy.assert_awaited_once()
    kwargs = spy.await_args.kwargs
    assert "observer" in kwargs["text"]
    assert "не дышит" in kwargs["text"]


# Сценарий: дедуп держит повторный алерт под капотом (Redis-ключ)
@pytest.mark.asyncio
async def test_alert_dedup_on_second_check(fake_redis_client) -> None:
    engine = _make_engine()

    with patch("apps.health_watchdog.main.notify_recipients", AsyncMock(return_value=True)):
        first = await check_worker_heartbeats(
            fake_redis_client,
            expected_workers=["enable"],
            engine=engine,
        )
        second = await check_worker_heartbeats(
            fake_redis_client,
            expected_workers=["enable"],
            engine=engine,
        )

    assert first == 1
    assert second == 0
    assert await fake_redis_client.get("health:alerted:enable") == "1"


# Сценарий: несколько ожидаемых воркеров — алертим по каждому отсутствующему
@pytest.mark.asyncio
async def test_multiple_workers_partial_missing(fake_redis_client) -> None:
    await fake_redis_client.set("worker:heartbeat:observer", "alive", ex=60)
    await fake_redis_client.set("worker:heartbeat:disable", "alive", ex=60)
    # enable и meta_api отсутствуют
    engine = _make_engine()
    notified_categories: list[str] = []

    async def fake_notify(eng, redis, *, category, text):
        notified_categories.append(text)
        return True

    with patch("apps.health_watchdog.main.notify_recipients", fake_notify):
        alerted = await check_worker_heartbeats(
            fake_redis_client,
            expected_workers=["observer", "disable", "enable", "meta_api"],
            engine=engine,
        )

    assert alerted == 2
    assert any("enable" in t for t in notified_categories)
    assert any("meta_api" in t for t in notified_categories)


# Сценарий: observer:runtime отсутствует → отдельный алерт о stale
@pytest.mark.asyncio
async def test_observer_runtime_missing_alerts(fake_redis_client) -> None:
    engine = _make_engine()

    with patch("apps.health_watchdog.main.notify_recipients", AsyncMock(return_value=True)) as spy:
        sent = await check_observer_runtime(
            fake_redis_client,
            engine=engine,
        )

    assert sent is True
    spy.assert_awaited_once()
    kwargs = spy.await_args.kwargs
    assert "observer:runtime" in kwargs["text"]
    assert "missing" in kwargs["text"]


# Сценарий: observer:runtime свежий → молчим
@pytest.mark.asyncio
async def test_observer_runtime_fresh_no_alert(fake_redis_client) -> None:
    now = datetime.now(timezone.utc)
    payload = json.dumps({"worker_status": "scanning", "updated_at": now.isoformat()})
    await fake_redis_client.set("observer:runtime", payload, ex=60)
    engine = _make_engine()

    with patch("apps.health_watchdog.main.notify_recipients", AsyncMock(return_value=True)) as spy:
        sent = await check_observer_runtime(
            fake_redis_client,
            engine=engine,
        )

    assert sent is False
    spy.assert_not_awaited()


# Сценарий: observer:runtime со старым updated_at → алерт + дедуп
@pytest.mark.asyncio
async def test_observer_runtime_stale_alerts_once(fake_redis_client) -> None:
    stale_ts = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    payload = json.dumps({"worker_status": "idle", "updated_at": stale_ts})
    await fake_redis_client.set("observer:runtime", payload, ex=60)
    engine = _make_engine()

    with patch("apps.health_watchdog.main.notify_recipients", AsyncMock(return_value=True)) as spy:
        first = await check_observer_runtime(fake_redis_client, engine=engine)
        second = await check_observer_runtime(fake_redis_client, engine=engine)

    assert first is True
    assert second is False
    assert spy.await_count == 1


# Сценарий: run_one_check проверяет и воркеров, и observer:runtime
@pytest.mark.asyncio
async def test_run_one_check_combines_both(fake_redis_client) -> None:
    await fake_redis_client.set("worker:heartbeat:observer", "alive", ex=60)
    engine = _make_engine()
    notified: list[str] = []

    async def fake_notify(eng, redis, *, category, text):
        notified.append(text)
        return True

    with patch("apps.health_watchdog.main.notify_recipients", fake_notify):
        # check_autostop_channel нужен engine с реальной БД — патчим
        with patch(
            "apps.health_watchdog.main.check_autostop_channel", AsyncMock(return_value=False)
        ):
            await run_one_check(
                fake_redis_client,
                expected_workers=["observer", "disable"],
                engine=engine,
            )

    # disable отсутствует + observer:runtime отсутствует → 2 алерта
    assert len(notified) == 2
    assert any("disable" in t for t in notified)
    assert any("observer:runtime" in t for t in notified)


# Сценарий: notify_recipients вернул False (нет recipients) → дедуп-ключ не ставится
@pytest.mark.asyncio
async def test_no_recipients_does_not_set_dedup(fake_redis_client) -> None:
    engine = _make_engine()

    with patch("apps.health_watchdog.main.notify_recipients", AsyncMock(return_value=False)):
        await check_worker_heartbeats(
            fake_redis_client,
            expected_workers=["observer"],
            engine=engine,
        )

    # При неудачной доставке дедуп-ключ НЕ ставится (алерт не теряется навсегда)
    assert await fake_redis_client.get("health:alerted:observer") is None

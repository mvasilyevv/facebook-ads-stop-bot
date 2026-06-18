# -*- coding: utf-8 -*-
"""Unit-тесты graceful-idle telegram_poller'а без токена.

Покрывают онбординг чистой инсталляции: при пустом telegram_config poller НЕ
завершается (раньше делал `return` → supervisord BACKOFF → run.sh падал целиком),
а уходит в idle, продолжает heartbeat и горячо подхватывает токен из БД,
введённый через Settings (UI).
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack, suppress
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _build_patches(state: dict, calls: dict, created: list) -> tuple[ExitStack, AsyncMock]:
    """Готовит ExitStack с моками всех внешних зависимостей main_loop.

    state["token"] управляет тем, что вернёт load_telegram_config: None (idle)
    или конфиг с токеном. calls/created — счётчики для ассертов.
    """

    async def fake_load(engine):
        calls["load"] += 1
        tok = state["token"]
        if not tok:
            return None
        return SimpleNamespace(bot_token=tok, poller_offset=0)

    def fake_client_ctor(bot_token, http_client):
        created.append(bot_token)
        c = MagicMock()

        async def fake_get_updates(offset=None, timeout_seconds=0):
            calls["get_updates"] += 1
            await asyncio.sleep(0.005)  # точка отмены + не спинит CPU
            return []

        c.get_updates = fake_get_updates
        return c

    engine = MagicMock()
    engine.dispose = AsyncMock()

    fake_hb = MagicMock()
    fake_hb.set = AsyncMock()
    fake_hb.aclose = AsyncMock()
    fake_redis_mod = MagicMock()
    fake_redis_mod.from_url = MagicMock(return_value=fake_hb)

    fake_pubsub = MagicMock()
    fake_pubsub.close = AsyncMock()

    touch_hb = AsyncMock()

    es = ExitStack()
    p = "apps.telegram_poller.main."
    es.enter_context(patch(p + "create_async_engine", return_value=engine))
    es.enter_context(patch(p + "load_telegram_config", fake_load))
    es.enter_context(patch(p + "TelegramBotClient", fake_client_ctor))
    es.enter_context(patch(p + "RedisPubSub", return_value=fake_pubsub))
    es.enter_context(patch(p + "redis_asyncio", fake_redis_mod))
    es.enter_context(patch(p + "touch_poller_heartbeat", touch_hb))
    es.enter_context(patch(p + "save_poller_offset", AsyncMock()))
    # heartbeat детерминированно каждую итерацию (-1 < любой now-last) + быстрый idle-reload
    es.enter_context(patch(p + "_HEARTBEAT_INTERVAL_SECONDS", -1))
    es.enter_context(patch(p + "_IDLE_RELOAD_INTERVAL_SECONDS", 0.01))
    return es, touch_hb


# Сценарий: токена нет всё время → main_loop остаётся в idle (не выходит), heartbeat идёт, client не создаётся.
@pytest.mark.asyncio
async def test_main_loop_stays_alive_without_token() -> None:
    from apps.telegram_poller.main import main_loop

    state = {"token": None}
    calls = {"load": 0, "get_updates": 0}
    created: list[str] = []

    es, touch_hb = _build_patches(state, calls, created)
    with es:
        task = asyncio.create_task(main_loop("postgresql+asyncpg://t/t"))
        await asyncio.sleep(0.15)
        # Главное свойство (анти-регресс): не вышел сам при пустом config.
        if task.done():
            task.result()  # пробросит исключение, если упал с ошибкой
            pytest.fail("main_loop завершился без токена — должен оставаться в idle")
        assert created == [], "client не должен создаваться без токена"
        assert calls["get_updates"] == 0, "polling не должен идти без токена"
        assert calls["load"] >= 1, "config должен периодически перечитываться в idle"
        assert touch_hb.await_count >= 1, "heartbeat должен идти даже в idle"
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


# Сценарий: старт без токена (idle) → токен введён в UI → poller сам поднимает client и начинает polling.
@pytest.mark.asyncio
async def test_main_loop_picks_up_token_after_idle() -> None:
    from apps.telegram_poller.main import main_loop

    state = {"token": None}
    calls = {"load": 0, "get_updates": 0}
    created: list[str] = []

    es, _touch_hb = _build_patches(state, calls, created)
    with es:
        task = asyncio.create_task(main_loop("postgresql+asyncpg://t/t"))
        await asyncio.sleep(0.1)
        assert not task.done() and created == []  # пока idle
        # Пользователь ввёл токен через UI — БД теперь отдаёт конфиг.
        state["token"] = "BOT123"
        for _ in range(400):  # ждём горячего подхвата (макс ~2с)
            await asyncio.sleep(0.005)
            if calls["get_updates"] >= 1:
                break
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    assert created == ["BOT123"], f"client должен создаться с новым токеном, created={created}"
    assert calls["get_updates"] >= 1, "polling должен начаться после ввода токена"

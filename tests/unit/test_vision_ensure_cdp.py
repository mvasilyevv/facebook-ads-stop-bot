# -*- coding: utf-8 -*-
"""Unit-тесты graceful-контракта POST /vision/ensure-cdp (bootstrap из run.sh).

Контракт: эндпоинт всегда возвращает 200 с {ok,status,action,message} и НЕ падает 5xx,
даже если browser-agent недоступен — иначе run.sh снова словил бы ложный warning.
"""

from __future__ import annotations

import pytest

import apps.api.routers.v1.settings_vision as m


# CDP уже готов → ok=true, status=READY, action=none; reconnect НЕ вызывается.
@pytest.mark.asyncio
async def test_ensure_cdp_already_ready(monkeypatch):
    called = {"reconnect": False}

    async def fake_runtime(redis):
        return {"cdp_ready": True, "cdp_port": 9222}

    async def fake_reconnect(engine, settings):
        called["reconnect"] = True

    monkeypatch.setattr(m, "_read_runtime_from_redis", fake_runtime)
    monkeypatch.setattr(m, "_reconnect_browser", fake_reconnect)

    resp = await m.post_vision_ensure_cdp(engine=None, redis=None, settings=None)
    assert resp.ok is True
    assert resp.status == "READY"
    assert resp.action == "none"
    assert called["reconnect"] is False


# CDP не готов, reconnect успешен → ok=true, status=RECONNECTED, action=reconnect.
@pytest.mark.asyncio
async def test_ensure_cdp_reconnect_success(monkeypatch):
    calls = {"n": 0}

    async def fake_runtime(redis):
        # Первый вызов — CDP не готов; после reconnect — готов с портом.
        calls["n"] += 1
        ready = calls["n"] > 1
        return {"cdp_ready": ready, "cdp_port": 9333 if ready else None}

    async def fake_reconnect(engine, settings):
        return None

    monkeypatch.setattr(m, "_read_runtime_from_redis", fake_runtime)
    monkeypatch.setattr(m, "_reconnect_browser", fake_reconnect)

    resp = await m.post_vision_ensure_cdp(engine=None, redis=None, settings=None)
    assert resp.ok is True
    assert resp.status == "RECONNECTED"
    assert resp.action == "reconnect"


# CDP не готов, browser-agent недоступен (reconnect бросает) → ok=false, БЕЗ 5xx.
@pytest.mark.asyncio
async def test_ensure_cdp_agent_down_graceful(monkeypatch):
    async def fake_runtime(redis):
        return {"cdp_ready": False, "cdp_port": None}

    async def fake_reconnect(engine, settings):
        raise RuntimeError("gRPC browser-agent недоступен")

    monkeypatch.setattr(m, "_read_runtime_from_redis", fake_runtime)
    monkeypatch.setattr(m, "_reconnect_browser", fake_reconnect)

    # Не должно бросать — graceful 200 с ok=false (run.sh покажет мягкий warning).
    resp = await m.post_vision_ensure_cdp(engine=None, redis=None, settings=None)
    assert resp.ok is False
    assert resp.status == "UNAVAILABLE"
    assert resp.action == "reconnect"

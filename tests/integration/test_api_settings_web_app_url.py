# -*- coding: utf-8 -*-
"""Integration: web_app_url в settings/telegram (BL-15 Этап 1).

Хранение — system_config (key='web_app_url'), без миграции. GET-фолбэк на .env.
Endpoint открытый (как остальной settings-роутер desktop-фронта), токен не нужен.
Cleanup: DELETE system_config row.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app


def _make_app(engine, redis):
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: engine
    # settings/telegram использует DepRedis (кэш bot_username) — без lifespan
    # app.state.redis пуст, подменяем на fakeredis.
    app.dependency_overrides[get_redis] = lambda: redis
    return app


@pytest_asyncio.fixture
async def clean_web_app_url(pg_engine):
    """Удаляет system_config(web_app_url) до и после теста (изоляция)."""

    async def _wipe():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM system_config WHERE key = 'web_app_url'"))

    await _wipe()
    yield
    await _wipe()


# PUT https-URL → GET возвращает его (round-trip через system_config)
@pytest.mark.asyncio
async def test_web_app_url_put_get_roundtrip(pg_engine, fake_redis_client, clean_web_app_url):
    app = _make_app(pg_engine, fake_redis_client)
    url = "https://app.example.com/tma/"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        put = await ac.put("/api/settings/telegram/web-app-url", json={"web_app_url": url})
        get = await ac.get("/api/settings/telegram")
    assert put.status_code == 200, put.text
    assert put.json()["web_app_url"] == url
    assert get.json()["web_app_url"] == url


# PUT не-HTTPS → 422 (требование Telegram Mini Apps)
@pytest.mark.asyncio
async def test_web_app_url_rejects_http(pg_engine, fake_redis_client, clean_web_app_url):
    app = _make_app(pg_engine, fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.put(
            "/api/settings/telegram/web-app-url", json={"web_app_url": "http://insecure.example"}
        )
    assert resp.status_code == 422


# PUT пустой строки → очистка (в system_config записан null, GET берёт фолбэк .env)
@pytest.mark.asyncio
async def test_web_app_url_clear(pg_engine, fake_redis_client, clean_web_app_url):
    app = _make_app(pg_engine, fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        await ac.put(
            "/api/settings/telegram/web-app-url", json={"web_app_url": "https://x.example/"}
        )
        cleared = await ac.put("/api/settings/telegram/web-app-url", json={"web_app_url": ""})
    assert cleared.status_code == 200
    # После очистки stored=None → GET вернёт config.web_app_url (.env), не сохранённый URL.
    assert cleared.json()["web_app_url"] != "https://x.example/"

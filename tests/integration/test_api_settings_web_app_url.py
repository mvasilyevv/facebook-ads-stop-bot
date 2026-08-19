# -*- coding: utf-8 -*-
"""Integration: DB-authoritative web_app_url и one-shot env bootstrap.

Хранение — system_config (key='web_app_url'). Runtime env fallback отсутствует.
Endpoint открытый (как остальной settings-роутер desktop-фронта), токен не нужен.
Cleanup: DELETE system_config row.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine
from apps.api.main import create_app
from core.telegram.web_app_url import (
    bootstrap_web_app_url_from_env,
    load_web_app_url,
)


def _make_app(engine):
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: engine
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


# PUT https-URL → GET возвращает его канонический вид (round-trip через system_config)
@pytest.mark.asyncio
async def test_web_app_url_put_get_roundtrip(pg_engine, clean_web_app_url):
    app = _make_app(pg_engine)
    # Хвостовой слэш срезается на границе: из этой базы собирается ссылка на
    # мини-приложение, и два написания одного адреса дали бы две разные ссылки
    # на один экран. Раньше значение проходило насквозь — это была дыра.
    url = "https://app.example.com/tma/"
    normalized = "https://app.example.com/tma"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        put = await ac.put("/api/settings/telegram/web-app-url", json={"web_app_url": url})
        get = await ac.get("/api/settings/telegram")
    assert put.status_code == 200, put.text
    assert put.json()["web_app_url"] == normalized
    assert get.json()["web_app_url"] == normalized


# PUT не-HTTPS → 422 (требование Telegram Mini Apps)
@pytest.mark.asyncio
async def test_web_app_url_rejects_http(pg_engine, clean_web_app_url):
    app = _make_app(pg_engine)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.put(
            "/api/settings/telegram/web-app-url", json={"web_app_url": "http://insecure.example"}
        )
    assert resp.status_code == 422


# PUT пустой строки → явный DB tombstone; env больше не восстанавливает URL.
@pytest.mark.asyncio
async def test_web_app_url_clear(pg_engine, clean_web_app_url):
    app = _make_app(pg_engine)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        await ac.put(
            "/api/settings/telegram/web-app-url", json={"web_app_url": "https://x.example/"}
        )
        cleared = await ac.put("/api/settings/telegram/web-app-url", json={"web_app_url": ""})
    assert cleared.status_code == 200
    assert cleared.json()["web_app_url"] is None


@pytest.mark.asyncio
async def test_web_app_url_env_bootstrap_is_idempotent_and_concurrency_safe(
    pg_engine,
    clean_web_app_url,
) -> None:
    url = "https://bootstrap.example/tma/"
    settings = SimpleNamespace(web_app_url=url)

    results = await asyncio.gather(
        *(bootstrap_web_app_url_from_env(pg_engine, settings=settings) for _ in range(4))
    )

    assert results.count(True) == 1
    # Значение из окружения тоже приводится к каноническому виду — иначе
    # написание в .env определяло бы форму ссылки на мини-приложение.
    assert await load_web_app_url(pg_engine) == "https://bootstrap.example/tma"


@pytest.mark.asyncio
async def test_web_app_url_tombstone_blocks_env_reimport(
    pg_engine,
    clean_web_app_url,
) -> None:
    app = _make_app(pg_engine)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        cleared = await ac.put("/api/settings/telegram/web-app-url", json={"web_app_url": ""})
    assert cleared.json()["web_app_url"] is None

    inserted = await bootstrap_web_app_url_from_env(
        pg_engine,
        settings=SimpleNamespace(web_app_url="https://env.example/tma/"),
    )

    assert inserted is False
    assert await load_web_app_url(pg_engine) is None

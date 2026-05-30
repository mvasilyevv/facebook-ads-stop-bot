# -*- coding: utf-8 -*-
"""Integration: TMA auth + Bearer-guard (BL-15 Этап 0).

Money/security: токен выдаётся только при валидном initData И наличии активного
recipient'а; guard перепроверяет recipient на каждом запросе (немедленный отзыв).
Требует Postgres (pg_engine) + seeded_telegram_config (bot_token из conftest).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine
from apps.api.main import create_app

_BOT_TOKEN = "TEST_BOT_TOKEN_FAKE"  # совпадает с seeded_telegram_config


def _make_app(engine):
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: engine
    return app


def _sign_init_data(
    user: dict,
    *,
    auth_date: int | None = None,
    bot_token: str = _BOT_TOKEN,
    tamper: bool = False,
) -> str:
    """Собирает подписанный по HMAC initData (как Telegram WebApp)."""
    ad = auth_date if auth_date is not None else int(time.time())
    fields = {
        "user": json.dumps(user, separators=(",", ":")),
        "auth_date": str(ad),
        "query_id": "AAH-test",
    }
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    fields["hash"] = "deadbeefcafe" if tamper else digest
    return urllib.parse.urlencode(fields)


@pytest_asyncio.fixture
async def tma_recipient(pg_engine):
    """Фабрика активных recipient'ов с teardown-очисткой."""
    created: list[tuple[int, int]] = []

    async def _make(uid: int, role: str = "recipient") -> int:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_recipients (chat_id, telegram_user_id, username, role)
                    VALUES (:c, :u, :un, :r)
                    ON CONFLICT (chat_id, telegram_user_id)
                    DO UPDATE SET role = EXCLUDED.role, revoked_at = NULL
                    """
                ),
                {"c": uid, "u": uid, "un": f"user{uid}", "r": role},
            )
        created.append((uid, uid))
        return uid

    yield _make

    for cid, uid in created:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM telegram_recipients WHERE chat_id = :c AND telegram_user_id = :u"
                ),
                {"c": cid, "u": uid},
            )


# Валидный initData + активный recipient → 200, токен + роль
@pytest.mark.asyncio
async def test_auth_valid_returns_token(pg_engine, seeded_telegram_config, tma_recipient):
    uid = 7000001
    await tma_recipient(uid, role="owner")
    app = _make_app(pg_engine)
    init = _sign_init_data({"id": uid, "first_name": "Test"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post("/api/tma/auth", json={"init_data": init})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token"]
    assert body["role"] == "owner"


# Подделанный hash → 401
@pytest.mark.asyncio
async def test_auth_bad_hash_401(pg_engine, seeded_telegram_config, tma_recipient):
    uid = 7000002
    await tma_recipient(uid)
    app = _make_app(pg_engine)
    init = _sign_init_data({"id": uid}, tamper=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post("/api/tma/auth", json={"init_data": init})
    assert resp.status_code == 401


# Истёкший auth_date → 401
@pytest.mark.asyncio
async def test_auth_expired_401(pg_engine, seeded_telegram_config, tma_recipient):
    uid = 7000003
    await tma_recipient(uid)
    app = _make_app(pg_engine)
    init = _sign_init_data({"id": uid}, auth_date=int(time.time()) - 200_000)  # > 86400
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post("/api/tma/auth", json={"init_data": init})
    assert resp.status_code == 401


# Валидный initData, но пользователя нет в recipients → 403
@pytest.mark.asyncio
async def test_auth_no_recipient_403(pg_engine, seeded_telegram_config):
    app = _make_app(pg_engine)
    init = _sign_init_data({"id": 7999999})  # нет в recipients
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post("/api/tma/auth", json={"init_data": init})
    assert resp.status_code == 403


# Нет telegram_config (бот не настроен) → 503
@pytest.mark.asyncio
async def test_auth_no_telegram_config_503(pg_engine, tma_recipient):
    # Без seeded_telegram_config — гарантируем отсутствие строки.
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_config WHERE singleton_key = 'default'"))
    uid = 7000004
    await tma_recipient(uid)
    app = _make_app(pg_engine)
    init = _sign_init_data({"id": uid})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post("/api/tma/auth", json={"init_data": init})
    assert resp.status_code == 503


# Полный цикл: auth → token → GET /tma/me под guard → 200
@pytest.mark.asyncio
async def test_me_with_valid_token(pg_engine, seeded_telegram_config, tma_recipient):
    uid = 7000005
    await tma_recipient(uid, role="recipient")
    app = _make_app(pg_engine)
    init = _sign_init_data({"id": uid})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        auth = await ac.post("/api/tma/auth", json={"init_data": init})
        token = auth.json()["token"]
        me = await ac.get("/api/tma/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json() == {"telegram_user_id": uid, "role": "recipient"}


# Guard без токена → 401
@pytest.mark.asyncio
async def test_me_no_token_401(pg_engine):
    app = _make_app(pg_engine)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.get("/api/tma/me")
    assert resp.status_code == 401


# Guard с мусорным токеном → 401
@pytest.mark.asyncio
async def test_me_garbage_token_401(pg_engine):
    app = _make_app(pg_engine)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.get("/api/tma/me", headers={"Authorization": "Bearer not.a.valid.token"})
    assert resp.status_code == 401


# Немедленный отзыв: валидный токен, но recipient revoked → 403 (guard не доверяет токену)
@pytest.mark.asyncio
async def test_me_revoked_recipient_403(pg_engine, seeded_telegram_config, tma_recipient):
    uid = 7000006
    await tma_recipient(uid)
    app = _make_app(pg_engine)
    init = _sign_init_data({"id": uid})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        token = (await ac.post("/api/tma/auth", json={"init_data": init})).json()["token"]
        # Отзываем доступ ПОСЛЕ выдачи токена.
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE telegram_recipients SET revoked_at = NOW() WHERE telegram_user_id = :u"
                ),
                {"u": uid},
            )
        me = await ac.get("/api/tma/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 403

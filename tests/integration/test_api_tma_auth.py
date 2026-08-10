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
from pydantic import SecretStr
from sqlalchemy import text

from apps.api.deps import get_engine
from apps.api.main import create_app
from core.config import get_settings
from core.crypto import encrypt
from core.telegram.gateway import telegram_credential_fingerprint

_BOT_TOKEN = "TEST_BOT_TOKEN_FAKE"  # совпадает с seeded_telegram_config


@pytest.fixture(autouse=True)
def _configured_tma_session_secret(monkeypatch):
    """TMA integration exercises an explicitly configured signing boundary."""
    monkeypatch.setattr(
        get_settings(),
        "tma_session_secret",
        SecretStr("integration-only-tma-session-secret"),
    )


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


# Нет telegram_config (runtime не использует env как fallback) → 503
@pytest.mark.asyncio
async def test_auth_no_telegram_config_503(pg_engine, tma_recipient, monkeypatch):
    # Без seeded_telegram_config — гарантируем отсутствие строки.
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_config WHERE singleton_key = 'default'"))
    # Даже если TELEGRAM_BOT_TOKEN задан процессу, runtime обязан fail closed.
    monkeypatch.setattr(
        get_settings(),
        "telegram_bot_token",
        SecretStr("123456789:RUNTIME_MUST_NOT_IMPORT"),
    )
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


@pytest.mark.asyncio
async def test_shared_telegram_admin_reads_require_current_owner_role(
    pg_engine,
    seeded_telegram_config,
    tma_recipient,
    monkeypatch,
):
    recipient_uid = 7_000_051
    owner_uid = 7_000_052
    await tma_recipient(recipient_uid, role="recipient")
    await tma_recipient(owner_uid, role="owner")
    monkeypatch.setattr(get_settings(), "require_api_key", True)
    monkeypatch.setattr(
        "apps.api.middleware.api_key_auth.get_engine",
        lambda: pg_engine,
    )
    app = _make_app(pg_engine)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        recipient_token = (
            await client.post(
                "/api/tma/auth",
                json={"init_data": _sign_init_data({"id": recipient_uid})},
            )
        ).json()["token"]
        owner_token = (
            await client.post(
                "/api/tma/auth",
                json={"init_data": _sign_init_data({"id": owner_uid})},
            )
        ).json()["token"]
        denied = await client.get(
            "/api/settings/telegram/recipients",
            headers={"Authorization": f"Bearer {recipient_token}"},
        )
        allowed = await client.get(
            "/api/settings/telegram/recipients",
            headers={"Authorization": f"Bearer {owner_token}"},
        )

    assert denied.status_code == 403
    assert denied.json()["code"] == "owner_role_required"
    assert allowed.status_code == 200
    assert allowed.json()["total"] >= 2


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


@pytest.mark.asyncio
async def test_bot_rotation_revokes_old_tma_http_authority_and_new_generation_succeeds(
    pg_engine,
    seeded_telegram_config,
    tma_recipient,
    monkeypatch,
):
    """A session is authority from one configured bot generation, not just a JWT."""
    uid = 7_000_061
    await tma_recipient(uid, role="owner")
    monkeypatch.setattr(get_settings(), "require_api_key", True)
    monkeypatch.setattr(
        "apps.api.middleware.api_key_auth.get_engine",
        lambda: pg_engine,
    )
    app = _make_app(pg_engine)
    replacement_token = "TEST_BOT_TOKEN_REPLACEMENT"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        old_auth = await client.post(
            "/api/tma/auth",
            json={"init_data": _sign_init_data({"id": uid})},
        )
        assert old_auth.status_code == 200, old_auth.text
        old_token = old_auth.json()["token"]

        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    UPDATE telegram_config
                    SET bot_token_encrypted = :encrypted,
                        bot_token_fingerprint = :fingerprint,
                        is_enabled = TRUE,
                        webhook_generation = 2,
                        webhook_applied_generation = NULL,
                        webhook_operation = 'configure',
                        webhook_desired_url =
                            'https://test.invalid/api/v1/integrations/telegram/webhook?bot_generation=2',
                        webhook_state = 'pending',
                        webhook_configured_at = NULL,
                        updated_at = NOW()
                    WHERE singleton_key = 'default'
                    """
                ),
                {
                    "encrypted": encrypt(replacement_token),
                    "fingerprint": bytes.fromhex(
                        telegram_credential_fingerprint(replacement_token)
                    ),
                },
            )

        old_me = await client.get(
            "/api/tma/me",
            headers={"Authorization": f"Bearer {old_token}"},
        )
        old_write = await client.post(
            "/api/operator/ads/not-a-real-ad/pause",
            headers={
                "Authorization": f"Bearer {old_token}",
                "Idempotency-Key": "stale-generation-must-not-write",
            },
            json={},
        )
        pending_auth = await client.post(
            "/api/tma/auth",
            json={
                "init_data": _sign_init_data(
                    {"id": uid},
                    bot_token=replacement_token,
                )
            },
        )
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    UPDATE telegram_config
                    SET webhook_applied_generation = webhook_generation,
                        webhook_state = 'configured',
                        webhook_configured_at = NOW(),
                        updated_at = NOW()
                    WHERE singleton_key = 'default'
                      AND webhook_generation = 2
                    """
                )
            )
        new_auth = await client.post(
            "/api/tma/auth",
            json={
                "init_data": _sign_init_data(
                    {"id": uid},
                    bot_token=replacement_token,
                )
            },
        )
        assert new_auth.status_code == 200, new_auth.text
        new_me = await client.get(
            "/api/tma/me",
            headers={"Authorization": f"Bearer {new_auth.json()['token']}"},
        )

    assert old_me.status_code == 401
    assert old_write.status_code == 401
    assert old_write.json()["code"] == "invalid_tma_session"
    assert pending_auth.status_code == 503
    assert new_me.status_code == 200
    assert new_me.json() == {"telegram_user_id": uid, "role": "owner"}

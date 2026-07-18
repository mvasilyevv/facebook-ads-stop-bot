from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import fakeredis.aioredis
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from apps.api import deps
from apps.api.routers import panel_auth
from core.auth.panel_access import (
    PANEL_SESSION_COOKIE,
    TelegramSigningKeyNotFound,
    create_panel_session,
    load_panel_session,
)
from core.config import Settings


def _settings(**overrides) -> Settings:
    values = {
        "require_api_key": False,
        "telegram_oidc_client_id": "12345",
        "telegram_oidc_client_secret": SecretStr("client-secret-that-is-long-enough-123"),
        "telegram_oidc_redirect_uri": "https://app.adpulse.su/auth/telegram/callback",
        "panel_auth_session_ttl_seconds": 43_200,
        "panel_auth_ticket_ttl_seconds": 60,
        "panel_auth_state_ttl_seconds": 600,
        "panel_auth_owner_recheck_seconds": 60,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _app(redis, settings) -> FastAPI:
    app = FastAPI()
    app.include_router(panel_auth.router)
    app.dependency_overrides[deps.get_engine] = lambda: object()
    app.dependency_overrides[deps.get_redis] = lambda: redis
    app.dependency_overrides[deps.get_settings] = lambda: settings
    return app


async def _value(value):
    return value


async def _begin(client: AsyncClient, return_to: str = "/campaigns") -> str:
    response = await client.get(
        "/auth/telegram/start", params={"return_to": return_to}, follow_redirects=False
    )
    assert response.status_code == 303
    return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


def _mock_oidc(monkeypatch, *, user_id: int, role: str = "owner") -> None:
    async def recipient(_engine, *, telegram_user_id):
        assert telegram_user_id == user_id
        return SimpleNamespace(telegram_user_id=user_id, role=role)

    monkeypatch.setattr(panel_auth, "find_recipient_by_telegram_user_id", recipient)
    monkeypatch.setattr(panel_auth, "_exchange_code", lambda **_kwargs: _value("token"))
    monkeypatch.setattr(panel_auth, "_load_jwks", lambda _redis, **_kwargs: _value({"keys": []}))
    monkeypatch.setattr(
        panel_auth,
        "verify_telegram_id_token",
        lambda *_args, **_kwargs: {"id": user_id},
    )


@pytest.mark.asyncio
async def test_authorization_code_callback_uses_one_time_ticket_before_cookie(
    monkeypatch,
):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    settings = _settings()
    _mock_oidc(monkeypatch, user_id=123456)
    app = _app(redis, settings)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.adpulse.su"
    ) as client:
        state = await _begin(client)
        callback = await client.get(
            "/auth/telegram/callback",
            params={"state": state, "code": "valid"},
            follow_redirects=False,
        )
        assert callback.status_code == 303
        assert callback.headers["location"].startswith("/auth/redeem?ticket=")
        assert PANEL_SESSION_COOKIE not in callback.headers.get("set-cookie", "")

        redeemed = await client.get(callback.headers["location"], follow_redirects=False)
        assert redeemed.status_code == 303 and redeemed.headers["location"] == "/campaigns"
        cookie = redeemed.headers["set-cookie"]
        assert f"{PANEL_SESSION_COOKIE}=" in cookie
        assert all(flag in cookie for flag in ("HttpOnly", "Secure", "SameSite=lax"))
        assert "Path=/" in cookie and "Max-Age=43200" in cookie
        assert (await client.get("/auth/verify")).status_code == 200

        assert (await client.get(callback.headers["location"])).status_code == 403
        assert (
            await client.get(
                "/auth/telegram/callback",
                params={"state": state, "code": "valid"},
            )
        ).status_code == 403
    await redis.aclose()


@pytest.mark.asyncio
async def test_verify_returns_api_401_but_redirects_navigation(monkeypatch):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app = _app(redis, _settings())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.adpulse.su"
    ) as client:
        api = await client.get(
            "/auth/verify",
            headers={"X-Forwarded-Uri": "/api/stats/today?range=1d"},
            follow_redirects=False,
        )
        navigation = await client.get(
            "/auth/verify",
            headers={"X-Forwarded-Uri": "/campaigns"},
            follow_redirects=False,
        )
    assert api.status_code == 401
    assert api.json()["login_url"] == api.headers["x-auth-login-url"]
    assert navigation.status_code == 303
    assert navigation.headers["location"].startswith("/auth/login?")
    await redis.aclose()


@pytest.mark.asyncio
async def test_non_owner_cannot_redeem_a_panel_session(monkeypatch):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    _mock_oidc(monkeypatch, user_id=123456, role="recipient")
    app = _app(redis, _settings())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.adpulse.su"
    ) as client:
        state = await _begin(client)
        denied = await client.get(
            "/auth/telegram/callback",
            params={"state": state, "code": "valid"},
            follow_redirects=False,
        )
    assert denied.status_code == 403
    assert PANEL_SESSION_COOKIE not in denied.headers.get("set-cookie", "")
    await redis.aclose()


@pytest.mark.asyncio
async def test_owner_revocation_invalidates_existing_session(monkeypatch):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    settings = _settings(panel_auth_owner_recheck_seconds=0)
    token, _ = await create_panel_session(
        redis,
        telegram_user_id=123456,
        role="owner",
        source="telegram_oidc",
        ttl=43_200,
    )

    async def revoked(_engine, *, telegram_user_id):
        assert telegram_user_id == 123456
        return None

    monkeypatch.setattr(panel_auth, "find_recipient_by_telegram_user_id", revoked)
    app = _app(redis, settings)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.adpulse.su"
    ) as client:
        client.cookies.set(PANEL_SESSION_COOKIE, token, domain="app.adpulse.su")
        denied = await client.get("/auth/verify", follow_redirects=False)
    assert denied.status_code == 303
    assert await load_panel_session(redis, token) is None
    await redis.aclose()


@pytest.mark.asyncio
async def test_unknown_kid_forces_exactly_one_jwks_refresh(monkeypatch):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    settings = _settings()
    refreshes: list[bool] = []
    verification_calls = 0

    async def recipient(_engine, *, telegram_user_id):
        return SimpleNamespace(telegram_user_id=telegram_user_id, role="owner")

    async def load(_redis, *, force_refresh=False):
        refreshes.append(force_refresh)
        return {"keys": []}

    def verify(*_args, **_kwargs):
        nonlocal verification_calls
        verification_calls += 1
        if verification_calls == 1:
            raise TelegramSigningKeyNotFound("rotated")
        return {"id": 123456}

    monkeypatch.setattr(panel_auth, "find_recipient_by_telegram_user_id", recipient)
    monkeypatch.setattr(panel_auth, "_exchange_code", lambda **_kwargs: _value("token"))
    monkeypatch.setattr(panel_auth, "_load_jwks", load)
    monkeypatch.setattr(panel_auth, "verify_telegram_id_token", verify)
    app = _app(redis, settings)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.adpulse.su"
    ) as client:
        state = await _begin(client)
        callback = await client.get(
            "/auth/telegram/callback",
            params={"state": state, "code": "valid"},
            follow_redirects=False,
        )
    assert callback.status_code == 303
    assert refreshes == [False, True]
    assert verification_calls == 2
    await redis.aclose()

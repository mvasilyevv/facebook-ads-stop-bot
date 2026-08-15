from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from apps.api import deps
from apps.api.routers import panel_auth
from core.auth.panel_access import (
    PANEL_SESSION_COOKIE,
    OidcAttempt,
    PanelSession,
    PanelTicket,
    TelegramSigningKeyNotFound,
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
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _app(settings: Settings, engine: object | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(panel_auth.router)
    app.dependency_overrides[deps.get_engine] = lambda: engine or object()
    app.dependency_overrides[deps.get_settings] = lambda: settings
    return app


async def _value(value):
    return value


@pytest.mark.asyncio
async def test_telegram_start_persists_state_through_engine_without_redis(monkeypatch):
    engine = object()
    saved: list[tuple[object, str, OidcAttempt, int]] = []

    async def save(db, state, attempt, ttl):
        saved.append((db, state, attempt, ttl))

    monkeypatch.setattr(panel_auth, "save_oidc_attempt", save)
    app = _app(_settings(), engine)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.adpulse.su"
    ) as client:
        response = await client.get(
            "/auth/telegram/start",
            params={"return_to": "/campaigns"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    assert len(saved) == 1
    saved_engine, saved_state, saved_attempt, saved_ttl = saved[0]
    assert (saved_engine, saved_state, saved_ttl) == (engine, state, 600)
    assert saved_attempt.return_to == "/campaigns"
    assert deps.get_redis not in app.dependency_overrides


@pytest.mark.asyncio
async def test_verify_returns_api_401_but_redirects_navigation():
    app = _app(_settings())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.adpulse.su"
    ) as client:
        api = await client.get(
            "/auth/verify",
            headers={"X-Forwarded-Uri": "/api/operator/snapshot?window=today"},
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


@pytest.mark.asyncio
async def test_verify_emits_immutable_server_derived_principal(monkeypatch):
    session = PanelSession(123456, "owner", "telegram_oidc", 10, 1000)
    monkeypatch.setattr(
        panel_auth,
        "load_panel_session",
        lambda _engine, _token: _value(session),
    )
    app = _app(_settings())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.adpulse.su"
    ) as client:
        client.cookies.set(PANEL_SESSION_COOKIE, "opaque-session", domain="app.adpulse.su")
        response = await client.get("/auth/verify")
    assert response.status_code == 200
    assert response.headers["x-verified-operator-principal"] == "panel:123456"


@pytest.mark.asyncio
async def test_unknown_kid_forces_exactly_one_jwks_refresh(monkeypatch):
    refreshes: list[bool] = []
    verification_calls = 0
    attempt = OidcAttempt("nonce", "verifier", "/")

    async def recipient(_engine, *, telegram_user_id):
        return SimpleNamespace(telegram_user_id=telegram_user_id, role="owner")

    async def load(*, force_refresh=False):
        refreshes.append(force_refresh)
        return {"keys": []}

    def verify(*_args, **_kwargs):
        nonlocal verification_calls
        verification_calls += 1
        if verification_calls == 1:
            raise TelegramSigningKeyNotFound("rotated")
        return {"id": 123456}

    grant = PanelTicket(123456, "telegram_oidc", "/", 10, 70)
    monkeypatch.setattr(panel_auth, "consume_oidc_attempt", lambda *_args: _value(attempt))
    monkeypatch.setattr(panel_auth, "find_recipient_by_telegram_user_id", recipient)
    monkeypatch.setattr(panel_auth, "_exchange_code", lambda **_kwargs: _value("token"))
    monkeypatch.setattr(panel_auth, "_load_jwks", load)
    monkeypatch.setattr(panel_auth, "verify_telegram_id_token", verify)
    monkeypatch.setattr(
        panel_auth,
        "create_panel_ticket",
        lambda *_args, **_kwargs: _value(("ticket", grant)),
    )
    app = _app(_settings())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.adpulse.su"
    ) as client:
        callback = await client.get(
            "/auth/telegram/callback",
            params={"state": "state", "code": "valid"},
            follow_redirects=False,
        )
    assert callback.status_code == 303
    assert callback.headers["location"] == "/auth/redeem"
    assert urlsplit(callback.headers["location"]).query == ""
    assert "ticket" not in callback.headers["location"]
    assert "httponly" in callback.headers["set-cookie"].lower()
    assert refreshes == [False, True]
    assert verification_calls == 2

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text

from apps.api.deps import get_engine, get_settings
from apps.api.main import create_app
from apps.api.routers.panel_auth import _PANEL_TICKET_COOKIE, _exchange_code
from core.auth.panel_access import PANEL_SESSION_COOKIE, TELEGRAM_TOKEN_URL
from core.config import Settings


@pytest_asyncio.fixture
async def panel_recipient(pg_engine):
    created: list[int] = []

    async def _create(user_id: int, role: str) -> None:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_recipients
                        (chat_id, telegram_user_id, username, role)
                    VALUES (:uid, :uid, :username, :role)
                    ON CONFLICT (chat_id, telegram_user_id)
                    DO UPDATE SET role = EXCLUDED.role, revoked_at = NULL
                    """
                ),
                {"uid": user_id, "username": f"panel_{user_id}", "role": role},
            )
        created.append(user_id)

    yield _create

    if created:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM telegram_recipients WHERE telegram_user_id = ANY(:ids)"),
                {"ids": created},
            )


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


class _UnavailableRedis:
    def __getattr__(self, name: str):
        raise AssertionError(f"panel auth touched unavailable Redis: {name}")


def _app(pg_engine, settings: Settings):
    app = create_app()
    app.state.redis = _UnavailableRedis()
    app.dependency_overrides[get_engine] = lambda: pg_engine
    app.dependency_overrides[get_settings] = lambda: settings
    return app


async def _async_value(value):
    return value


async def _start(client: AsyncClient, return_to: str = "/") -> str:
    response = await client.get(
        "/auth/telegram/start", params={"return_to": return_to}, follow_redirects=False
    )
    assert response.status_code == 303
    return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


@pytest.mark.asyncio
async def test_owner_flow_uses_callback_ticket_then_secure_12h_cookie(
    pg_engine, panel_recipient, monkeypatch
):
    owner_id = 88000001
    await panel_recipient(owner_id, "owner")
    app = _app(pg_engine, _settings())
    monkeypatch.setattr(
        "apps.api.routers.panel_auth._exchange_code", lambda **_kwargs: _async_value("token")
    )
    monkeypatch.setattr(
        "apps.api.routers.panel_auth._load_jwks",
        lambda **_kwargs: _async_value({"keys": []}),
    )
    monkeypatch.setattr(
        "apps.api.routers.panel_auth.verify_telegram_id_token",
        lambda *_args, **_kwargs: {"id": owner_id},
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.adpulse.su"
    ) as client:
        login = await client.get("/auth/login?return_to=/campaigns")
        assert login.status_code == 200 and "Войти через Telegram" in login.text

        state = await _start(client, "/campaigns")
        callback = await client.get(
            "/auth/telegram/callback",
            params={"state": state, "code": "valid"},
            follow_redirects=False,
        )
        assert callback.status_code == 303
        # Одноразовый ticket передаётся только в HttpOnly cookie: query string
        # оседает в browser history, Referer и access logs.
        assert callback.headers["location"] == "/auth/redeem"
        assert urlsplit(callback.headers["location"]).query == ""
        callback_cookie = callback.headers["set-cookie"]
        assert PANEL_SESSION_COOKIE not in callback.headers.get("set-cookie", "")
        assert f"{_PANEL_TICKET_COOKIE}=" in callback_cookie
        assert "HttpOnly" in callback_cookie and "Secure" in callback_cookie
        assert "SameSite=strict" in callback_cookie
        assert "Path=/" in callback_cookie and "Max-Age=60" in callback_cookie
        ticket = client.cookies.get(_PANEL_TICKET_COOKIE)
        assert ticket
        async with pg_engine.connect() as conn:
            stored = (
                (
                    await conn.execute(
                        text("SELECT encode(ticket_digest, 'hex') FROM panel_login_tickets")
                    )
                )
                .scalars()
                .all()
            )
        assert len(stored) == 1 and ticket not in stored

        redeemed = await client.get(callback.headers["location"], follow_redirects=False)
        assert redeemed.status_code == 303 and redeemed.headers["location"] == "/campaigns"
        cookie = redeemed.headers["set-cookie"]
        assert f"{PANEL_SESSION_COOKIE}=" in cookie
        assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
        assert "Path=/" in cookie and "Max-Age=43200" in cookie
        verified = await client.get("/auth/verify")
        assert verified.status_code == 200
        assert verified.headers["x-verified-operator-principal"] == f"panel:{owner_id}"

        replay = await client.get(callback.headers["location"], follow_redirects=False)
        assert replay.status_code == 403
        assert "Ссылка входа недействительна" in replay.text
        assert PANEL_SESSION_COOKIE not in replay.headers.get("set-cookie", "")

        logout = await client.get("/auth/logout", follow_redirects=False)
        assert logout.status_code == 303
        assert "Max-Age=0" in logout.headers["set-cookie"]
        denied = await client.get(
            "/auth/verify",
            headers={"X-Forwarded-Uri": "/api/operator/snapshot"},
            follow_redirects=False,
        )
        assert denied.status_code == 401
        assert denied.headers["x-auth-login-url"].startswith("/auth/login?")


@pytest.mark.asyncio
async def test_state_replay_and_non_owner_fail_closed(pg_engine, panel_recipient, monkeypatch):
    user_id = 88000002
    await panel_recipient(user_id, "recipient")
    app = _app(pg_engine, _settings())
    monkeypatch.setattr(
        "apps.api.routers.panel_auth._exchange_code", lambda **_kwargs: _async_value("token")
    )
    monkeypatch.setattr(
        "apps.api.routers.panel_auth._load_jwks",
        lambda **_kwargs: _async_value({"keys": []}),
    )
    monkeypatch.setattr(
        "apps.api.routers.panel_auth.verify_telegram_id_token",
        lambda *_args, **_kwargs: {"id": user_id},
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.adpulse.su"
    ) as client:
        state = await _start(client)
        denied = await client.get(
            "/auth/telegram/callback",
            params={"state": state, "code": "valid"},
            follow_redirects=False,
        )
        # Публичный текст намеренно не различает non-owner и другие отказы OIDC,
        # но смысловой инвариант остаётся: ticket/session не выдаются.
        assert denied.status_code == 403 and "Telegram Login не подтверждён" in denied.text
        assert _PANEL_TICKET_COOKIE not in denied.headers.get("set-cookie", "")
        assert PANEL_SESSION_COOKIE not in denied.headers.get("set-cookie", "")
        async with pg_engine.connect() as conn:
            ticket_count = await conn.scalar(
                text(
                    "SELECT COUNT(*) FROM panel_login_tickets "
                    "WHERE telegram_user_id = :telegram_user_id"
                ),
                {"telegram_user_id": user_id},
            )
        assert ticket_count == 0
        replay = await client.get(
            "/auth/telegram/callback",
            params={"state": state, "code": "valid"},
            follow_redirects=False,
        )
        assert replay.status_code == 403 and "Telegram Login не подтверждён" in replay.text
        assert _PANEL_TICKET_COOKIE not in replay.headers.get("set-cookie", "")
        assert PANEL_SESSION_COOKIE not in replay.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_role_revocation_invalidates_existing_session_on_next_request(
    pg_engine, panel_recipient, monkeypatch
):
    owner_id = 88000003
    await panel_recipient(owner_id, "owner")
    app = _app(pg_engine, _settings())
    monkeypatch.setattr(
        "apps.api.routers.panel_auth._exchange_code", lambda **_kwargs: _async_value("token")
    )
    monkeypatch.setattr(
        "apps.api.routers.panel_auth._load_jwks",
        lambda **_kwargs: _async_value({"keys": []}),
    )
    monkeypatch.setattr(
        "apps.api.routers.panel_auth.verify_telegram_id_token",
        lambda *_args, **_kwargs: {"id": owner_id},
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.adpulse.su"
    ) as client:
        state = await _start(client)
        callback = await client.get(
            "/auth/telegram/callback",
            params={"state": state, "code": "valid"},
            follow_redirects=False,
        )
        await client.get(callback.headers["location"], follow_redirects=False)
        assert (await client.get("/auth/verify")).status_code == 200
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE telegram_recipients SET revoked_at = NOW() "
                    "WHERE telegram_user_id = :uid"
                ),
                {"uid": owner_id},
            )
        denied = await client.get("/auth/verify", follow_redirects=False)
        assert denied.status_code == 303
        assert denied.headers["location"].startswith("/auth/login?")


@pytest.mark.asyncio
@respx.mock
async def test_token_exchange_matches_telegram_basic_auth_and_pkce_contract():
    route = respx.post(TELEGRAM_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"id_token": "signed-id-token"})
    )
    result = await _exchange_code(
        client_id="12345",
        client_secret="secret-1",
        redirect_uri="https://app.adpulse.su/auth/telegram/callback",
        code="authorization-code",
        verifier="pkce-verifier",
    )
    assert result == "signed-id-token"
    request = route.calls.last.request
    assert request.headers["Authorization"].startswith("Basic ")
    assert parse_qs(request.content.decode()) == {
        "grant_type": ["authorization_code"],
        "code": ["authorization-code"],
        "redirect_uri": ["https://app.adpulse.su/auth/telegram/callback"],
        "client_id": ["12345"],
        "code_verifier": ["pkce-verifier"],
    }

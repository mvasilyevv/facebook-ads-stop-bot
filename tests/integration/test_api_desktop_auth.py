from __future__ import annotations

from datetime import datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text

from apps.api.deps import get_engine, get_settings
from apps.api.main import create_app
from apps.api.routers.desktop_auth import get_desktop_readiness_probe
from core.auth.desktop_access import (
    DESKTOP_SESSION_COOKIE,
    consume_desktop_ticket,
    create_desktop_ticket,
)
from core.auth.panel_access import PANEL_SESSION_COOKIE, create_panel_session
from core.auth.tma import issue_session_token
from core.config import Settings


@pytest_asyncio.fixture
async def desktop_users(pg_engine):
    owner_id = 89000001
    recipient_id = 89000002
    async with pg_engine.begin() as conn:
        for uid, role in ((owner_id, "owner"), (recipient_id, "recipient")):
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_recipients (chat_id, telegram_user_id, username, role)
                    VALUES (:uid, :uid, :username, :role)
                    ON CONFLICT (chat_id, telegram_user_id)
                    DO UPDATE SET role = :role, revoked_at = NULL
                    """
                ),
                {"uid": uid, "username": f"desktop_{role}", "role": role},
            )
    yield owner_id, recipient_id
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM telegram_recipients "
                "WHERE telegram_user_id IN (:owner_id, :recipient_id)"
            ),
            {"owner_id": owner_id, "recipient_id": recipient_id},
        )


def _settings(owner_id: int) -> Settings:
    return Settings(
        _env_file=None,
        require_api_key=False,
        api_key=SecretStr("desktop-api-key"),
        desktop_public_origin="https://desktop.adpulse.su",
        desktop_access_ticket_ttl_seconds=300,
        desktop_access_session_ttl_seconds=43_200,
        desktop_owner_telegram_user_id=owner_id,
        tma_session_secret=SecretStr("test-tma-secret"),
        # Кэш readyz отключён: тест подменяет пробу между вызовами и проверяет
        # оба состояния endpoint'а, кэшированный ответ исказил бы второй вызов.
        desktop_readiness_cache_seconds=0,
    )


def _app(pg_engine, fake_redis_client, settings):
    app = create_app()
    app.state.redis = fake_redis_client
    app.dependency_overrides[get_engine] = lambda: pg_engine
    app.dependency_overrides[get_settings] = lambda: settings
    return app


def _web_headers(**overrides: str) -> dict[str, str]:
    headers = {
        "Origin": "https://app.adpulse.su",
        "X-API-Key": "desktop-api-key",
    }
    headers.update(overrides)
    return headers


async def _set_panel_owner_cookie(client: AsyncClient, engine, owner_id: int) -> None:
    token, _ = await create_panel_session(
        engine,
        telegram_user_id=owner_id,
        role="owner",
        source="telegram_oidc",
        ttl=43_200,
    )
    client.cookies.set(PANEL_SESSION_COOKIE, token, domain="app.adpulse.su")


@pytest.mark.asyncio
async def test_web_owner_launch_has_documented_no_body_contract(
    pg_engine, fake_redis_client, desktop_users
):
    owner_id, _ = desktop_users
    app = _app(pg_engine, fake_redis_client, _settings(owner_id))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.adpulse.su"
    ) as client:
        await _set_panel_owner_cookie(client, pg_engine, owner_id)
        response = await client.post("/api/desktop/launch", headers=_web_headers())

    assert response.status_code == 200
    assert response.json()["url"].startswith(
        "https://desktop.adpulse.su/desktop-auth/redeem?ticket="
    )
    assert response.json()["transport"] == "kasm"
    assert datetime.fromisoformat(response.json()["expires_at"])
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    operation = app.openapi()["paths"]["/api/desktop/launch"]["post"]
    assert "requestBody" not in operation


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"X-API-Key": "desktop-api-key"}, 403),
        ({"Origin": "https://evil.example", "X-API-Key": "desktop-api-key"}, 403),
        ({"Origin": "https://app.adpulse.su"}, 401),
        ({"Origin": "https://app.adpulse.su", "X-API-Key": "wrong"}, 401),
    ],
)
async def test_web_launch_validates_exact_origin_and_api_key(
    pg_engine, fake_redis_client, desktop_users, headers, status
):
    owner_id, _ = desktop_users
    app = _app(pg_engine, fake_redis_client, _settings(owner_id))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.adpulse.su"
    ) as client:
        response = await client.post("/api/desktop/launch", headers=headers)
    assert response.status_code == status


@pytest.mark.asyncio
async def test_web_launch_uses_explicit_owner_and_rejects_revocation(
    pg_engine, fake_redis_client, desktop_users
):
    owner_id, _ = desktop_users
    settings = _settings(owner_id)
    app = _app(pg_engine, fake_redis_client, settings)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.adpulse.su"
    ) as client:
        await _set_panel_owner_cookie(client, pg_engine, owner_id)
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("UPDATE telegram_recipients SET revoked_at=NOW() WHERE telegram_user_id=:uid"),
                {"uid": owner_id},
            )
        response = await client.post("/api/desktop/launch", headers=_web_headers())
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_tma_bearer_launch_records_real_owner_and_rejects_recipient(
    pg_engine,
    fake_redis_client,
    desktop_users,
    seeded_telegram_config,
):
    owner_id, recipient_id = desktop_users
    settings = _settings(owner_id)
    app = _app(pg_engine, fake_redis_client, settings)
    owner_token = issue_session_token(
        str(owner_id),
        settings.tma_session_ttl_seconds,
        "test-tma-secret",
        bot_generation=1,
    )
    recipient_token = issue_session_token(
        str(recipient_id),
        settings.tma_session_ttl_seconds,
        "test-tma-secret",
        bot_generation=1,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.adpulse.su"
    ) as client:
        launched = await client.post(
            "/api/desktop/launch", headers={"Authorization": f"Bearer {owner_token}"}
        )
        denied = await client.post(
            "/api/desktop/launch", headers={"Authorization": f"Bearer {recipient_token}"}
        )

    assert launched.status_code == 200
    ticket = launched.json()["url"].split("ticket=", 1)[1]
    grant = await consume_desktop_ticket(fake_redis_client, ticket)
    assert grant.telegram_user_id == owner_id
    assert grant.source == "telegram_mini_app"
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_redeem_cookie_verify_logout_and_replay(pg_engine, fake_redis_client, desktop_users):
    owner_id, _ = desktop_users
    ticket, _ = await create_desktop_ticket(
        fake_redis_client,
        telegram_user_id=owner_id,
        source="telegram_mini_app",
        expected_hostname="desktop.adpulse.su",
        ttl=300,
    )
    app = _app(pg_engine, fake_redis_client, _settings(owner_id))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://desktop.adpulse.su"
    ) as client:
        redeemed = await client.get(f"/desktop-auth/redeem?ticket={ticket}", follow_redirects=False)
        token = redeemed.cookies.get(DESKTOP_SESSION_COOKIE)
        cookie_header = {"Cookie": f"{DESKTOP_SESSION_COOKIE}={token}"}
        verified = await client.get(
            "/desktop-auth/verify",
            headers={**cookie_header, "Remote-User": "attacker"},
            follow_redirects=False,
        )
        logged_out = await client.post(
            "/desktop/logout", headers=cookie_header, follow_redirects=False
        )
        denied = await client.get(
            "/desktop-auth/verify", headers=cookie_header, follow_redirects=False
        )
        replay = await client.get(f"/desktop-auth/redeem?ticket={ticket}", follow_redirects=False)

    assert redeemed.status_code == 303
    assert redeemed.headers["location"] == "/"
    cookie = redeemed.headers["set-cookie"]
    assert f"{DESKTOP_SESSION_COOKIE}=" in cookie
    assert "Path=/" in cookie
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert "Max-Age=43200" in cookie
    assert verified.status_code == 200
    assert verified.headers["remote-user"] == "adpulse-desktop"
    assert logged_out.status_code == 303
    assert denied.status_code == 303
    assert replay.status_code == 403


@pytest.mark.asyncio
async def test_revoke_invalidates_warm_desktop_session_on_next_verify(
    pg_engine,
    fake_redis_client,
    desktop_users,
):
    owner_id, _ = desktop_users
    ticket, _ = await create_desktop_ticket(
        fake_redis_client,
        telegram_user_id=owner_id,
        source="telegram_mini_app",
        expected_hostname="desktop.adpulse.su",
        ttl=300,
    )
    app = _app(pg_engine, fake_redis_client, _settings(owner_id))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://desktop.adpulse.su"
    ) as client:
        redeemed = await client.get(
            f"/desktop-auth/redeem?ticket={ticket}",
            follow_redirects=False,
        )
        token = redeemed.cookies.get(DESKTOP_SESSION_COOKIE)
        cookie_header = {"Cookie": f"{DESKTOP_SESSION_COOKIE}={token}"}
        assert (await client.get("/desktop-auth/verify", headers=cookie_header)).status_code == 200

        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE telegram_recipients SET revoked_at = NOW() "
                    "WHERE telegram_user_id = :owner_id"
                ),
                {"owner_id": owner_id},
            )

        denied = await client.get(
            "/desktop-auth/verify",
            headers=cookie_header,
            follow_redirects=False,
        )

    assert denied.status_code == 303
    assert denied.headers["location"].startswith("https://app.adpulse.su/remote-desktop")
    assert not await fake_redis_client.keys("desktop_access:v4:session:*")


@pytest.mark.asyncio
async def test_removed_legacy_desktop_routes_are_not_registered(
    pg_engine, fake_redis_client, desktop_users
):
    owner_id, _ = desktop_users
    app = _app(pg_engine, fake_redis_client, _settings(owner_id))
    paths = app.openapi()["paths"]
    assert "/api/desktop/launch" in paths
    for path in (
        "/desktop-auth/connect",
        "/desktop-auth/recovery",
        "/desktop-auth/launch-recovery",
        "/desktop-auth/launch-url-recovery",
        "/auth/desktop/session",
        "/auth/desktop/launch",
    ):
        assert path not in paths


class _ReadinessProbe:
    def __init__(self, checks):
        self.checks = checks

    async def check(self, settings):
        del settings
        return self.checks


@pytest.mark.asyncio
async def test_desktop_readyz_is_separate_and_fail_closed(
    pg_engine, fake_redis_client, desktop_users
):
    owner_id, _ = desktop_users
    app = _app(pg_engine, fake_redis_client, _settings(owner_id))
    app.dependency_overrides[get_desktop_readiness_probe] = lambda: _ReadinessProbe(
        {"configured": True, "auth_challenge": False, "authenticated": False}
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.adpulse.su"
    ) as client:
        failed = await client.get("/desktop-readyz")
    assert failed.status_code == 503
    assert failed.json() == {
        "status": "not_ready",
        "checks": {"configured": True, "auth_challenge": False, "authenticated": False},
    }
    assert "password" not in failed.text.lower()

    app.dependency_overrides[get_desktop_readiness_probe] = lambda: _ReadinessProbe(
        {"configured": True, "auth_challenge": True, "authenticated": True}
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.adpulse.su"
    ) as client:
        ready = await client.get("/desktop-readyz")
        core_ready = await client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ok"
    assert core_ready.status_code != 503 or core_ready.json() != ready.json()

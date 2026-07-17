from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text

from apps.api.deps import get_engine, get_settings
from apps.api.main import create_app
from core.auth.desktop_access import create_desktop_ticket
from core.config import Settings


@pytest_asyncio.fixture
async def desktop_owner(pg_engine):
    owner_id = 89000001
    async with pg_engine.begin() as conn:
        previous_owners = (
            await conn.execute(
                text(
                    """
                    SELECT chat_id, telegram_user_id
                    FROM telegram_recipients
                    WHERE role = 'owner' AND revoked_at IS NULL
                      AND telegram_user_id <> :uid
                    """
                ),
                {"uid": owner_id},
            )
        ).all()
        await conn.execute(
            text(
                """
                UPDATE telegram_recipients
                SET revoked_at = NOW()
                WHERE role = 'owner' AND revoked_at IS NULL
                  AND telegram_user_id <> :uid
                """
            ),
            {"uid": owner_id},
        )
        await conn.execute(
            text(
                """
                INSERT INTO telegram_recipients (chat_id, telegram_user_id, username, role)
                VALUES (:uid, :uid, 'desktop_owner', 'owner')
                ON CONFLICT (chat_id, telegram_user_id)
                DO UPDATE SET role = 'owner', revoked_at = NULL
                """
            ),
            {"uid": owner_id},
        )
    yield owner_id
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM telegram_recipients WHERE telegram_user_id = :uid"),
            {"uid": owner_id},
        )
        for chat_id, telegram_user_id in previous_owners:
            await conn.execute(
                text(
                    """
                    UPDATE telegram_recipients
                    SET revoked_at = NULL
                    WHERE chat_id = :chat_id AND telegram_user_id = :telegram_user_id
                    """
                ),
                {"chat_id": chat_id, "telegram_user_id": telegram_user_id},
            )


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        require_api_key=False,
        api_key=SecretStr("recovery-secret"),
        desktop_access_ticket_ttl_seconds=60,
        desktop_access_session_ttl_seconds=3600,
        desktop_access_owner_recheck_seconds=0,
        desktop_guacamole_json_secret=SecretStr("00112233445566778899aabbccddeeff"),
        desktop_guacamole_token_ttl_seconds=60,
        desktop_vnc_password=SecretStr("vnc-pass"),
    )


def _app(pg_engine, fake_redis_client):
    settings = _settings()
    app = create_app()
    app.state.redis = fake_redis_client
    app.dependency_overrides[get_engine] = lambda: pg_engine
    app.dependency_overrides[get_settings] = lambda: settings
    return app


@pytest.mark.asyncio
async def test_direct_connect_without_desktop_cookie_returns_to_panel(pg_engine, fake_redis_client):
    app = _app(pg_engine, fake_redis_client)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://desktop.adpulse.su"
    ) as client:
        response = await client.get("/desktop-auth/connect", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "https://app.adpulse.su/remote-desktop"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_panel_launch_issues_single_use_cross_host_ticket(
    pg_engine, fake_redis_client, desktop_owner
):
    app = _app(pg_engine, fake_redis_client)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.adpulse.su"
    ) as client:
        denied = await client.get("/desktop-auth/launch-recovery", follow_redirects=False)
        launched = await client.get(
            "/desktop-auth/launch-recovery",
            headers={"X-Panel-Recovery-Key": "recovery-secret"},
            follow_redirects=False,
        )
        redeemed = await client.get(launched.headers["location"], follow_redirects=False)

    assert denied.status_code == 404
    assert launched.status_code == 303
    assert launched.headers["location"].startswith(
        "https://desktop.adpulse.su/desktop-auth/redeem?ticket="
    )
    assert launched.headers["cache-control"] == "no-store"
    assert redeemed.status_code == 303
    assert redeemed.headers["location"] == "/desktop-auth/connect"
    assert "adpulse_desktop_session=" in redeemed.headers["set-cookie"]


@pytest.mark.asyncio
async def test_ticket_redeem_creates_host_session_and_ticket_is_single_use(
    pg_engine, fake_redis_client, desktop_owner
):
    ticket, _ = await create_desktop_ticket(
        fake_redis_client,
        telegram_user_id=desktop_owner,
        source="telegram_mini_app",
        ttl=60,
    )
    app = _app(pg_engine, fake_redis_client)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://desktop.adpulse.su"
    ) as client:
        redeemed = await client.get(f"/desktop-auth/redeem?ticket={ticket}", follow_redirects=False)
        verified = await client.get("/desktop-auth/verify", follow_redirects=False)
        connected = await client.get("/desktop-auth/connect", follow_redirects=False)
        replay = await client.get(f"/desktop-auth/redeem?ticket={ticket}", follow_redirects=False)

    assert redeemed.status_code == 303
    assert redeemed.headers["location"] == "/desktop-auth/connect"
    cookie = redeemed.headers["set-cookie"]
    assert "adpulse_desktop_session=" in cookie
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert "Max-Age=3600" in cookie
    assert verified.status_code == 200
    assert verified.headers["x-desktop-telegram-user-id"] == str(desktop_owner)
    assert connected.status_code == 303
    assert connected.headers["location"].startswith("/guacamole/?data=")
    assert "vnc-pass" not in connected.headers["location"]
    assert replay.status_code == 403


@pytest.mark.asyncio
async def test_recovery_cookie_continues_through_guacamole_connect(
    pg_engine, fake_redis_client, desktop_owner
):
    app = _app(pg_engine, fake_redis_client)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://desktop.adpulse.su"
    ) as client:
        recovered = await client.get(
            "/desktop-auth/recovery",
            headers={"X-Panel-Recovery-Key": "recovery-secret"},
            follow_redirects=False,
        )
        connected = await client.get("/desktop-auth/connect", follow_redirects=False)

    assert recovered.status_code == 303
    assert recovered.headers["location"] == "/desktop-auth/connect"
    assert "adpulse_desktop_session=" in recovered.headers["set-cookie"]
    assert connected.status_code == 303
    assert connected.headers["location"].startswith("/guacamole/?data=")


@pytest.mark.asyncio
async def test_connect_fails_closed_when_guacamole_secrets_are_missing(
    pg_engine, fake_redis_client, desktop_owner
):
    ticket, _ = await create_desktop_ticket(
        fake_redis_client,
        telegram_user_id=desktop_owner,
        source="panel_telegram",
        ttl=60,
    )
    settings = _settings()
    settings.desktop_guacamole_json_secret = SecretStr("")
    app = create_app()
    app.state.redis = fake_redis_client
    app.dependency_overrides[get_engine] = lambda: pg_engine
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://desktop.adpulse.su"
    ) as client:
        await client.get(f"/desktop-auth/redeem?ticket={ticket}", follow_redirects=False)
        connected = await client.get("/desktop-auth/connect", follow_redirects=False)

    assert connected.status_code == 503
    assert "временно не настроен" in connected.text


@pytest.mark.asyncio
async def test_desktop_session_is_rejected_after_owner_revocation(
    pg_engine, fake_redis_client, desktop_owner
):
    ticket, _ = await create_desktop_ticket(
        fake_redis_client,
        telegram_user_id=desktop_owner,
        source="panel_telegram",
        ttl=60,
    )
    app = _app(pg_engine, fake_redis_client)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://desktop.adpulse.su"
    ) as client:
        await client.get(f"/desktop-auth/redeem?ticket={ticket}", follow_redirects=False)
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE telegram_recipients SET revoked_at = NOW() "
                    "WHERE telegram_user_id = :uid"
                ),
                {"uid": desktop_owner},
            )
        denied = await client.get("/desktop-auth/verify", follow_redirects=False)

    assert denied.status_code == 303
    assert denied.headers["location"] == "https://app.adpulse.su/remote-desktop"

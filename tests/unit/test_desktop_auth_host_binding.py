from __future__ import annotations

from types import SimpleNamespace

import fakeredis.aioredis
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from apps.api import deps
from apps.api.routers import desktop_auth
from core.auth.desktop_access import (
    DESKTOP_SESSION_COOKIE,
    create_desktop_ticket,
)
from core.config import Settings


@pytest.mark.asyncio
async def test_ticket_and_session_are_bound_to_desktop_hostname(monkeypatch):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    settings = Settings(
        _env_file=None,
        require_api_key=False,
        desktop_public_origin="https://desktop.adpulse.su",
    )

    async def recipient(_engine, *, telegram_user_id):
        return SimpleNamespace(telegram_user_id=telegram_user_id, role="owner")

    monkeypatch.setattr(desktop_auth, "find_recipient_by_telegram_user_id", recipient)
    app = FastAPI()
    app.include_router(desktop_auth.router)
    app.dependency_overrides[deps.get_engine] = lambda: object()
    app.dependency_overrides[deps.get_redis] = lambda: redis
    app.dependency_overrides[deps.get_settings] = lambda: settings

    wrong_host_ticket, _ = await create_desktop_ticket(
        redis,
        telegram_user_id=1001,
        source="web_panel",
        expected_hostname="desktop.adpulse.su",
        ttl=300,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.adpulse.su"
    ) as panel_client:
        wrong_host = await panel_client.get(
            f"/desktop-auth/redeem?ticket={wrong_host_ticket}", follow_redirects=False
        )
        replay = await panel_client.get(
            f"/desktop-auth/redeem?ticket={wrong_host_ticket}", follow_redirects=False
        )
    assert wrong_host.status_code == 403
    assert "другого hostname" in wrong_host.text
    assert replay.status_code == 403

    ticket, _ = await create_desktop_ticket(
        redis,
        telegram_user_id=1001,
        source="web_panel",
        expected_hostname="desktop.adpulse.su",
        ttl=300,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://desktop.adpulse.su"
    ) as desktop_client:
        redeemed = await desktop_client.get(
            f"/desktop-auth/redeem?ticket={ticket}", follow_redirects=False
        )
        verified = await desktop_client.get("/desktop-auth/verify", follow_redirects=False)
        token = redeemed.cookies.get(DESKTOP_SESSION_COOKIE)
        cross_host = await desktop_client.get(
            "/desktop-auth/verify",
            headers={
                "Host": "app.adpulse.su",
                "Cookie": f"{DESKTOP_SESSION_COOKIE}={token}",
            },
            follow_redirects=False,
        )

    assert redeemed.status_code == 303
    assert redeemed.headers["location"] == "/"
    cookie = redeemed.headers["set-cookie"]
    assert "Path=/" in cookie and "Secure" in cookie and "HttpOnly" in cookie
    assert verified.status_code == 200
    assert verified.headers["x-desktop-transport"] == "kasm"
    assert cross_host.status_code == 303
    await redis.aclose()

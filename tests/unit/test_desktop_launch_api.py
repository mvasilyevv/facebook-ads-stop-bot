from __future__ import annotations

from types import SimpleNamespace

import fakeredis.aioredis
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from apps.api import deps
from apps.api.routers.v1 import desktop as desktop_router
from apps.api.routers.v1 import tma as tma_router
from core.auth.desktop_access import consume_desktop_ticket
from core.auth.tma import issue_session_token
from core.config import Settings


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        require_api_key=False,
        api_key=SecretStr("api-key"),
        desktop_owner_telegram_user_id=1001,
        desktop_public_origin="https://app.adpulse.su",
        tma_session_secret=SecretStr("tma-secret"),
    )


def _app(redis, settings) -> FastAPI:
    app = FastAPI()
    app.include_router(desktop_router.router, prefix="/api")
    app.dependency_overrides[deps.get_engine] = lambda: object()
    app.dependency_overrides[deps.get_redis] = lambda: redis
    app.dependency_overrides[deps.get_settings] = lambda: settings
    return app


@pytest.mark.asyncio
async def test_web_and_tma_launch_use_explicit_verified_identities(monkeypatch):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    settings = _settings()

    async def recipient(_engine, *, telegram_user_id):
        if telegram_user_id in {1001, 2002}:
            return SimpleNamespace(
                telegram_user_id=telegram_user_id,
                chat_id=telegram_user_id,
                role="owner",
            )
        return None

    monkeypatch.setattr(desktop_router, "find_recipient_by_telegram_user_id", recipient)
    monkeypatch.setattr(tma_router, "find_recipient_by_telegram_user_id", recipient)
    app = _app(redis, settings)
    bearer = issue_session_token("2002", settings.tma_session_ttl_seconds, "tma-secret")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.adpulse.su"
    ) as client:
        web = await client.post(
            "/api/desktop/launch",
            headers={"Origin": "https://app.adpulse.su", "X-API-Key": "api-key"},
        )
        tma = await client.post(
            "/api/desktop/launch", headers={"Authorization": f"Bearer {bearer}"}
        )

    assert web.status_code == 200 and tma.status_code == 200
    assert web.headers["cache-control"] == "no-store"
    assert web.headers["referrer-policy"] == "no-referrer"
    web_grant = await consume_desktop_ticket(redis, web.json()["url"].split("ticket=", 1)[1])
    tma_grant = await consume_desktop_ticket(redis, tma.json()["url"].split("ticket=", 1)[1])
    assert (web_grant.telegram_user_id, web_grant.source) == (1001, "web_panel")
    assert (tma_grant.telegram_user_id, tma_grant.source) == (2002, "telegram_mini_app")
    await redis.aclose()


@pytest.mark.asyncio
async def test_launch_rejects_bad_web_transport_and_non_owner_tma(monkeypatch):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    settings = _settings()

    async def recipient(_engine, *, telegram_user_id):
        return SimpleNamespace(
            telegram_user_id=telegram_user_id,
            chat_id=telegram_user_id,
            role="recipient" if telegram_user_id == 2002 else "owner",
        )

    monkeypatch.setattr(desktop_router, "find_recipient_by_telegram_user_id", recipient)
    monkeypatch.setattr(tma_router, "find_recipient_by_telegram_user_id", recipient)
    app = _app(redis, settings)
    bearer = issue_session_token("2002", settings.tma_session_ttl_seconds, "tma-secret")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.adpulse.su"
    ) as client:
        wrong_origin = await client.post(
            "/api/desktop/launch",
            headers={"Origin": "https://evil.example", "X-API-Key": "api-key"},
        )
        wrong_key = await client.post(
            "/api/desktop/launch",
            headers={"Origin": "https://app.adpulse.su", "X-API-Key": "wrong"},
        )
        recipient_response = await client.post(
            "/api/desktop/launch", headers={"Authorization": f"Bearer {bearer}"}
        )
    assert wrong_origin.status_code == 403
    assert wrong_key.status_code == 401
    assert recipient_response.status_code == 403
    await redis.aclose()


@pytest.mark.asyncio
async def test_launch_fails_closed_for_non_production_public_origin():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    settings = _settings().model_copy(
        update={"desktop_public_origin": "https://desktop.adpulse.su"}
    )
    app = _app(redis, settings)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.adpulse.su"
    ) as client:
        response = await client.post(
            "/api/desktop/launch",
            headers={"Origin": "https://app.adpulse.su", "X-API-Key": "api-key"},
        )

    assert response.status_code == 503
    await redis.aclose()


def test_openapi_exposes_only_bodyless_public_desktop_launch():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    schema = _app(redis, _settings()).openapi()
    operation = schema["paths"]["/api/desktop/launch"]["post"]
    assert "requestBody" not in operation
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DesktopLaunchResponse"
    }

# -*- coding: utf-8 -*-
"""Интеграционные тесты для GET/PUT /api/settings/vision и POST /api/vision/reconnect.

Требует живой Postgres. gRPC вызовы мокаются — не нужен browser-agent.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine, get_meta_api_client
from apps.api.main import create_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app_client(pg_engine):
    """AsyncClient with PostgreSQL and no browser-agent channel."""
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine
    app.dependency_overrides[get_meta_api_client] = lambda: None

    # Очистка ДО теста: каждый тест получает независимый canonical config.
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM vision_config"))
        await conn.execute(text("DELETE FROM browser_operation_leases"))
        await conn.execute(text("DELETE FROM system_config WHERE key = 'browser_maintenance'"))
        await conn.execute(
            text("DELETE FROM task_queue WHERE idempotency_key LIKE 'vision-update-running-%'")
        )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

    # Очистка таблиц после теста.
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM vision_config"))
        await conn.execute(text("DELETE FROM browser_operation_leases"))
        await conn.execute(text("DELETE FROM system_config WHERE key = 'browser_maintenance'"))
        await conn.execute(
            text("DELETE FROM task_queue WHERE idempotency_key LIKE 'vision-update-running-%'")
        )


# ---------------------------------------------------------------------------
# GET /settings/vision
# ---------------------------------------------------------------------------


# Без config — token absent and the channel is explicitly unavailable.
@pytest.mark.asyncio
async def test_get_vision_no_config_returns_defaults(app_client) -> None:
    resp = await app_client.get("/api/settings/vision")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_token"] is False
    assert "token_source" not in data
    assert data["profile_id"] is None
    assert data["channel_status"] == "UNAVAILABLE"
    assert data["channel_message"] == "Vision is not configured in PostgreSQL"
    assert data["browser_contract_version"] is None
    assert data["browser_contract_compatible"] is False


@pytest.mark.asyncio
async def test_get_vision_ignores_credential_environment(
    pg_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VISION_X_TOKEN", "must-not-be-used")
    monkeypatch.setenv("VISION_PROFILE_ID", "must-not-be-used")
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM vision_config"))

    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine
    app.dependency_overrides[get_meta_api_client] = lambda: None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/settings/vision")

    assert resp.status_code == 200
    data = resp.json()
    assert data["has_token"] is False
    assert data["profile_id"] is None
    assert data["channel_status"] == "UNAVAILABLE"
    assert "token_source" not in data

    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM vision_config"))


@pytest.mark.asyncio
async def test_missing_db_config_cannot_be_false_green_from_live_channel(pg_engine) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM vision_config"))

    meta_client = MagicMock()
    meta_client.check_health = AsyncMock(
        return_value={
            "healthy": True,
            "detail": "ok",
            "browser_contract_version": 5,
            "session_id": "browser-session-ready",
            "vision_profile_id": "profile-ready",
            "probe_performed": True,
            "probe_ok": True,
        }
    )
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine
    app.dependency_overrides[get_meta_api_client] = lambda: meta_client

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/settings/vision")

    assert response.status_code == 200
    assert response.json()["channel_status"] == "UNAVAILABLE"
    meta_client.check_health.assert_not_awaited()


# Direct gRPC evidence is exposed without any Redis compatibility key.
@pytest.mark.asyncio
async def test_get_vision_with_ready_browser_channel(pg_engine) -> None:
    from core.crypto import encrypt

    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM vision_config"))
        await conn.execute(
            text(
                """
                INSERT INTO vision_config (x_token_encrypted, profile_id)
                VALUES (:token, 'profile-ready')
                """
            ),
            {"token": encrypt("ready-token")},
        )

    meta_client = MagicMock()
    meta_client.check_health = AsyncMock(
        return_value={
            "healthy": True,
            "detail": "ok",
            "browser_contract_version": 5,
            "session_id": "browser-session-ready",
            "vision_profile_id": "profile-ready",
            "probe_performed": True,
            "probe_ok": True,
        }
    )
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine
    app.dependency_overrides[get_meta_api_client] = lambda: meta_client

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/settings/vision")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["channel_status"] == "READY"
    assert payload["required_browser_contract_version"] == 5
    assert payload["browser_contract_version"] == 5
    assert payload["browser_contract_compatible"] is True
    assert payload["browser_session_id"] == "browser-session-ready"
    assert payload["live_profile_id"] == "profile-ready"
    assert payload["graph_probe_performed"] is True
    assert payload["graph_probe_ok"] is True
    meta_client.check_health.assert_awaited_once_with(
        full_probe=True,
        expected_profile_id="profile-ready",
    )

    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM vision_config"))


@pytest.mark.asyncio
async def test_get_vision_rejects_incompatible_browser_contract(pg_engine) -> None:
    from core.crypto import encrypt

    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM vision_config"))
        await conn.execute(
            text(
                """
                INSERT INTO vision_config (x_token_encrypted, profile_id)
                VALUES (:token, 'profile-ready')
                """
            ),
            {"token": encrypt("ready-token")},
        )

    meta_client = MagicMock()
    meta_client.check_health = AsyncMock(
        return_value={
            "healthy": True,
            "detail": "ok",
            "browser_contract_version": 4,
            "session_id": "browser-session-ready",
            "vision_profile_id": "profile-ready",
            "probe_performed": True,
            "probe_ok": True,
        }
    )
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine
    app.dependency_overrides[get_meta_api_client] = lambda: meta_client

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/settings/vision")

    payload = response.json()
    assert response.status_code == 200
    assert payload["channel_status"] == "DEGRADED"
    assert payload["required_browser_contract_version"] == 5
    assert payload["browser_contract_version"] == 4
    assert payload["browser_contract_compatible"] is False
    assert "incompatible" in payload["channel_message"]
    assert "required=5, observed=4" in payload["channel_message"]

    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM vision_config"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("health_patch", "message_fragment"),
    [
        (
            {
                "healthy": True,
                "vision_profile_id": "another-profile",
                "probe_performed": True,
                "probe_ok": True,
            },
            "does not match",
        ),
        (
            {
                "healthy": True,
                "vision_profile_id": "profile-ready",
                "probe_performed": False,
                "probe_ok": False,
            },
            "did not perform",
        ),
    ],
)
async def test_get_vision_rejects_false_green_identity_or_shallow_probe(
    pg_engine,
    health_patch: dict[str, object],
    message_fragment: str,
) -> None:
    from core.crypto import encrypt

    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM vision_config"))
        await conn.execute(
            text(
                """
                INSERT INTO vision_config (x_token_encrypted, profile_id)
                VALUES (:token, 'profile-ready')
                """
            ),
            {"token": encrypt("ready-token")},
        )

    meta_client = MagicMock()
    meta_client.check_health = AsyncMock(
        return_value={
            "healthy": True,
            "detail": "ok",
            "browser_contract_version": 5,
            "session_id": "browser-session-ready",
            "vision_profile_id": "profile-ready",
            "probe_performed": True,
            "probe_ok": True,
        }
        | health_patch
    )
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine
    app.dependency_overrides[get_meta_api_client] = lambda: meta_client

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/settings/vision")

    payload = response.json()
    assert response.status_code == 200
    assert payload["channel_status"] == "DEGRADED"
    assert message_fragment in payload["channel_message"]
    assert payload["browser_contract_compatible"] is True

    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM vision_config"))


# ---------------------------------------------------------------------------
# PUT /settings/vision
# ---------------------------------------------------------------------------


# PUT с x_token — после GET has_token=True, токен в БД зашифрован
@pytest.mark.asyncio
async def test_put_vision_x_token_sets_has_token(app_client, pg_engine) -> None:
    resp = await app_client.put(
        "/api/settings/vision",
        json={"x_token": "my_vision_token_123", "profile_id": "profile-abc"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_token"] is True
    assert "token_source" not in data
    assert data["profile_id"] == "profile-abc"

    # Проверяем, что токен в БД зашифрован (не хранится в открытом виде)
    from core.crypto import decrypt

    async with pg_engine.begin() as conn:
        result = await conn.execute(
            text("SELECT x_token_encrypted FROM vision_config WHERE singleton_key = 'default'")
        )
        row = result.first()
    assert row is not None
    stored_token = row[0]
    # Токен должен отличаться от оригинала (зашифрован)
    assert stored_token != "my_vision_token_123"
    # Но расшифроваться в оригинал
    assert decrypt(stored_token) == "my_vision_token_123"


# PUT, затем GET — has_token=True
@pytest.mark.asyncio
async def test_get_after_put_vision_shows_has_token(app_client) -> None:
    await app_client.put(
        "/api/settings/vision",
        json={"x_token": "test_token"},
    )
    resp = await app_client.get("/api/settings/vision")
    assert resp.status_code == 200
    assert resp.json()["has_token"] is True


# PUT только profile_id — токен не меняется
@pytest.mark.asyncio
async def test_put_vision_only_profile_id(app_client) -> None:
    # Сначала ставим токен
    await app_client.put("/api/settings/vision", json={"x_token": "initial_token"})
    # Потом обновляем только profile_id
    resp = await app_client.put("/api/settings/vision", json={"profile_id": "new-profile"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["profile_id"] == "new-profile"
    # Токен должен остаться
    assert data["has_token"] is True


@pytest.mark.asyncio
async def test_put_vision_rejects_removed_auto_restart_contract(app_client) -> None:
    resp = await app_client.put(
        "/api/settings/vision",
        json={"auto_restart_on_missing_cdp": False},
    )
    assert resp.status_code == 422
    assert "auto_restart_on_missing_cdp" in resp.text


@pytest.mark.asyncio
async def test_profile_change_waits_for_browser_work(
    app_client,
    pg_engine,
) -> None:
    from core.tasks.browser_fence import BrowserExclusiveMaintenance
    from core.tasks.queue import claim_next_task, create_task, mark_succeeded

    configured = await app_client.put(
        "/api/settings/vision",
        json={"x_token": "db-token", "profile_id": "profile-before"},
    )
    assert configured.status_code == 200
    task_id = await create_task(
        pg_engine,
        task_type="observer_scan",
        idempotency_key=f"vision-update-running-{uuid.uuid4().hex}",
        payload={"reason": "test"},
        requested_by="test",
        lane="background",
    )
    assert task_id is not None
    claim = await claim_next_task(
        pg_engine,
        task_type="observer_scan",
        lanes=("background",),
    )
    assert claim.task is not None

    def short_drain(engine, *, operation_kind):
        return BrowserExclusiveMaintenance(
            engine,
            operation_kind=operation_kind,
            drain_seconds=1,
        )

    with patch(
        "apps.api.routers.v1.settings_vision.BrowserExclusiveMaintenance",
        side_effect=short_drain,
    ):
        blocked = await app_client.put(
            "/api/settings/vision",
            json={"profile_id": "profile-after"},
        )
    assert blocked.status_code == 409

    assert claim.task.lease_owner is not None
    assert await mark_succeeded(
        pg_engine,
        task_id=task_id,
        result={"outcome": "CONFIRMED"},
        lease_owner=claim.task.lease_owner,
        lease_token=claim.task.lease_token,
    )


# ---------------------------------------------------------------------------
# POST /vision/reconnect
# ---------------------------------------------------------------------------


# POST reconnect через fake gRPC client — возвращает 200 {"status": "reconnected"}
@pytest.mark.asyncio
async def test_post_vision_reconnect_success(app_client) -> None:
    await app_client.put(
        "/api/settings/vision",
        json={"x_token": "db-token", "profile_id": "db-profile"},
    )
    # Мокаем BrowserAgentClient целиком
    mock_client = AsyncMock()
    mock_client.start = AsyncMock()
    mock_client.reconnect_browser = AsyncMock(return_value="session-123")
    mock_client.close = AsyncMock()

    with patch(
        "apps.api.routers.v1.settings_vision.BrowserAgentClient",
        return_value=mock_client,
    ):
        resp = await app_client.post("/api/vision/reconnect")

    assert resp.status_code == 200
    assert resp.json()["status"] == "reconnected"


# POST reconnect при gRPC ошибке — возвращает 503
@pytest.mark.asyncio
async def test_post_vision_reconnect_grpc_unavailable(app_client) -> None:
    import grpc

    await app_client.put(
        "/api/settings/vision",
        json={"x_token": "db-token", "profile_id": "db-profile"},
    )
    mock_client = AsyncMock()
    mock_client.start = AsyncMock()
    mock_client.close = AsyncMock()

    mock_client.reconnect_browser = AsyncMock(side_effect=grpc.RpcError())

    with patch(
        "apps.api.routers.v1.settings_vision.BrowserAgentClient",
        return_value=mock_client,
    ):
        resp = await app_client.post("/api/vision/reconnect")

    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_post_vision_reconnect_without_db_config_is_rejected(app_client) -> None:
    with patch("apps.api.routers.v1.settings_vision.BrowserAgentClient") as client_ctor:
        resp = await app_client.post("/api/vision/reconnect")

    assert resp.status_code == 409
    assert resp.json()["message"] == "Vision runtime не настроен"
    client_ctor.assert_not_called()


@pytest.mark.asyncio
async def test_post_vision_ensure_cdp_without_owner_is_unavailable(app_client) -> None:
    resp = await app_client.post("/api/vision/ensure-cdp")

    assert resp.status_code == 200
    assert resp.json() == {
        "ok": False,
        "status": "UNAVAILABLE",
        "action": "none",
        "message": "Platform maintenance ownership is missing or expired",
    }


@pytest.mark.asyncio
async def test_post_vision_ensure_cdp_without_db_config_is_unavailable(
    app_client,
    pg_engine,
) -> None:
    owner = uuid.uuid4().hex
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO system_config (key, value, description)
                VALUES (
                  'browser_maintenance',
                  jsonb_build_object(
                    'owner', CAST(:owner AS text),
                    'expires_at', clock_timestamp() + interval '5 minutes'
                  ),
                  'test'
                )
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value,
                    description = EXCLUDED.description,
                    updated_at = clock_timestamp()
                """
            ),
            {"owner": owner},
        )
    try:
        resp = await app_client.post(
            "/api/vision/ensure-cdp",
            headers={"X-FB-Agent-Browser-Maintenance-Owner": owner},
        )
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM system_config WHERE key = 'browser_maintenance'"))

    assert resp.status_code == 200
    assert resp.json() == {
        "ok": False,
        "status": "UNAVAILABLE",
        "action": "none",
        "message": "Vision is not configured in PostgreSQL",
    }

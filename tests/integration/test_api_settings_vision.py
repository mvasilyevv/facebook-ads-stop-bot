# -*- coding: utf-8 -*-
"""Интеграционные тесты для GET/PUT /api/settings/vision, POST /api/vision/reconnect
и GET /api/vision/profiles.

Требует живой Postgres (docker-compose:5433). Использует fakeredis для Redis.
gRPC вызовы мокаются — не нужен живой browser-agent.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis as fakeredis_aio  # type: ignore[import-not-found]
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def fake_redis():
    """Fakeredis async — без живого Redis-сервера."""
    return fakeredis_aio.FakeRedis()


@pytest_asyncio.fixture
async def app_client(pg_engine, fake_redis):
    """AsyncClient с FastAPI app, подключённым к реальному Postgres + fakeredis."""
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.state.redis = fake_redis

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

    # Очистка таблиц после теста.
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM vision_config"))


# ---------------------------------------------------------------------------
# GET /settings/vision
# ---------------------------------------------------------------------------


# Без config — has_token=False, runtime=null
@pytest.mark.asyncio
async def test_get_vision_no_config_returns_defaults(app_client) -> None:
    resp = await app_client.get("/api/settings/vision")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_token"] is False
    assert data["profile_id"] is None
    assert data["auto_restart_on_missing_cdp"] is True
    assert data["runtime_status"] is None
    assert data["cdp_ready"] is False
    assert data["cdp_port"] is None


# Без config, но с Redis heartbeat — runtime_status не null
@pytest.mark.asyncio
async def test_get_vision_with_redis_heartbeat(pg_engine, fake_redis) -> None:
    # Устанавливаем heartbeat как простую строку
    await fake_redis.set("worker:heartbeat:browser-agent", "alive")

    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.state.redis = fake_redis

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/settings/vision")

    assert resp.status_code == 200
    data = resp.json()
    # heartbeat есть → runtime_status не None
    assert data["runtime_status"] is not None


# Heartbeat с JSON-payload — cdp_ready и cdp_port парсятся
@pytest.mark.asyncio
async def test_get_vision_with_json_heartbeat(pg_engine, fake_redis) -> None:
    import json

    payload = json.dumps({"status": "ONLINE", "cdp_ready": True, "cdp_port": 9222, "message": "ok"})
    await fake_redis.set("worker:heartbeat:browser-agent", payload)

    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.state.redis = fake_redis

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/settings/vision")

    assert resp.status_code == 200
    data = resp.json()
    assert data["runtime_status"] == "ONLINE"
    assert data["cdp_ready"] is True
    assert data["cdp_port"] == 9222


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


# ---------------------------------------------------------------------------
# POST /vision/reconnect
# ---------------------------------------------------------------------------


# POST reconnect через fake gRPC client — возвращает 200 {"status": "reconnected"}
@pytest.mark.asyncio
async def test_post_vision_reconnect_success(app_client) -> None:
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

    mock_client = AsyncMock()
    mock_client.start = AsyncMock()
    mock_client.close = AsyncMock()

    # Создаём реальный gRPC RpcError
    rpc_error = MagicMock(spec=grpc.RpcError)
    rpc_error.__str__ = MagicMock(return_value="connection refused")
    mock_client.reconnect_browser = AsyncMock(side_effect=grpc.RpcError())

    with patch(
        "apps.api.routers.v1.settings_vision.BrowserAgentClient",
        return_value=mock_client,
    ):
        resp = await app_client.post("/api/vision/reconnect")

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /vision/profiles
# ---------------------------------------------------------------------------


# GET profiles — всегда 501 (ListProfiles не реализован)
@pytest.mark.asyncio
async def test_get_vision_profiles_returns_501(app_client) -> None:
    resp = await app_client.get("/api/vision/profiles")
    assert resp.status_code == 501
    assert "ListProfiles" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /settings/browser/validate-columns
# ---------------------------------------------------------------------------


# validate-columns проксирует gRPC и возвращает реальный результат (mock client)
@pytest.mark.asyncio
async def test_validate_columns_proxies_grpc_success(app_client) -> None:
    """validate-columns вызывает BrowserAgentClient.validate_columns(), не возвращает безусловный True."""
    mock_client = AsyncMock()
    mock_client.start = AsyncMock()
    mock_client.close = AsyncMock()
    # Мок возвращает реальный результат — колонки валидны
    mock_client.validate_columns = AsyncMock(
        return_value={
            "valid": True,
            "missing_columns": [],
            "found_columns": ["spend", "impressions"],
            "error_message": "",
        }
    )

    with patch(
        "apps.api.routers.v1.settings_vision.BrowserAgentClient",
        return_value=mock_client,
    ):
        resp = await app_client.get("/api/settings/browser/validate-columns")

    assert resp.status_code == 200
    data = resp.json()
    # Ключевая проверка: valid берётся из gRPC, не из хардкода
    assert data["valid"] is True
    assert data["missing_columns"] == []
    # Убеждаемся, что gRPC-метод был вызван
    mock_client.validate_columns.assert_awaited_once()


# validate-columns с missing_columns от gRPC → valid=False, список возвращается
@pytest.mark.asyncio
async def test_validate_columns_returns_missing_columns(app_client) -> None:
    """validate-columns корректно передаёт missing_columns от gRPC (не маскирует проблему)."""
    mock_client = AsyncMock()
    mock_client.start = AsyncMock()
    mock_client.close = AsyncMock()
    mock_client.validate_columns = AsyncMock(
        return_value={
            "valid": False,
            "missing_columns": ["delivery_status", "frequency"],
            "found_columns": ["spend"],
            "error_message": "Колонки не найдены в DOM",
        }
    )

    with patch(
        "apps.api.routers.v1.settings_vision.BrowserAgentClient",
        return_value=mock_client,
    ):
        resp = await app_client.get("/api/settings/browser/validate-columns")

    assert resp.status_code == 200
    data = resp.json()
    # Результат от gRPC пробрасывается без подмены
    assert data["valid"] is False
    assert "delivery_status" in data["missing_columns"]
    assert data["error_message"] is not None


# validate-columns при gRPC ошибке → 503 (не 200 с фейковым valid=true)
@pytest.mark.asyncio
async def test_validate_columns_grpc_error_returns_503(app_client) -> None:
    """При недоступности browser-agent → 503, НЕ фейк-true."""
    import grpc

    mock_client = AsyncMock()
    mock_client.start = AsyncMock()
    mock_client.close = AsyncMock()
    mock_client.validate_columns = AsyncMock(side_effect=grpc.RpcError())

    with patch(
        "apps.api.routers.v1.settings_vision.BrowserAgentClient",
        return_value=mock_client,
    ):
        resp = await app_client.get("/api/settings/browser/validate-columns")

    # Должно быть 503, а не 200 с valid=True
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /settings/browser/save-column-widths и apply-column-widths → 501
# ---------------------------------------------------------------------------


# save-column-widths — честный 501 (CaptureColumnWidths не реализован в python client)
@pytest.mark.asyncio
async def test_save_column_widths_returns_501(app_client) -> None:
    """save-column-widths возвращает 501, не молчаливый noop."""
    resp = await app_client.post("/api/settings/browser/save-column-widths")
    assert resp.status_code == 501
    assert "CaptureColumnWidths" in resp.json()["detail"]


# apply-column-widths — честный 501 (ApplyColumnWidths не реализован в python client)
@pytest.mark.asyncio
async def test_apply_column_widths_returns_501(app_client) -> None:
    """apply-column-widths возвращает 501, не молчаливый noop."""
    resp = await app_client.post("/api/settings/browser/apply-column-widths")
    assert resp.status_code == 501
    assert "ApplyColumnWidths" in resp.json()["detail"]

# -*- coding: utf-8 -*-
"""Интеграционные тесты роутера settings_observer.

Тесты используют AsyncClient + ASGITransport (как test_api_health.py), чтобы
async pg_engine fixture из conftest работала в том же event loop, что и app.

Паттерн:
    app = _make_app(engine=pg_engine, redis=fake_redis)
    async with AsyncClient(...) as ac:
        resp = await ac.get("/api/settings/observer")
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app


def _make_app(*, engine=None, redis=None):
    """Собирает FastAPI с явными override engine/redis."""
    app = create_app()
    if engine is not None:
        app.dependency_overrides[get_engine] = lambda: engine
    if redis is not None:
        app.dependency_overrides[get_redis] = lambda: redis
        app.state.redis = redis
    return app


@pytest_asyncio.fixture
async def clean_observer_config(pg_engine):
    """Сбрасывает singleton observer_config до server-defaults перед и после теста."""
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM observer_config"))
    yield
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM observer_config"))


# GET возвращает дефолтный singleton (is_scanning_enabled=false — scanning OFF by default).
@pytest.mark.asyncio
async def test_get_observer_settings_returns_defaults(
    pg_engine, fake_redis_client, clean_observer_config
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/settings/observer")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_scanning_enabled"] is False
    assert isinstance(data["default_interval_seconds"], int)
    assert data["auto_enable_recommendations"] is False
    assert "warning_percent_of_stop" not in data
    assert "cpc_warning_percent" not in data
    assert "cpl_warning_percent" not in data
    assert "cpr_warning_percent" not in data


# PUT обновляет поля, последующий GET отражает изменения.
@pytest.mark.asyncio
async def test_put_observer_settings_persists(pg_engine, fake_redis_client, clean_observer_config):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    body = {
        "is_scanning_enabled": False,
        "default_interval_seconds": 120,
        "auto_enable_recommendations": True,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        put_resp = await ac.put("/api/settings/observer", json=body)
        assert put_resp.status_code == 200

        get_resp = await ac.get("/api/settings/observer")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["is_scanning_enabled"] is False
    assert data["default_interval_seconds"] == 120
    assert data["auto_enable_recommendations"] is True


# PUT с interval_seconds=10 (меньше допустимого минимума 30) → 422.
@pytest.mark.asyncio
async def test_put_observer_settings_validates_interval(
    pg_engine, fake_redis_client, clean_observer_config
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    body = {
        "is_scanning_enabled": True,
        "default_interval_seconds": 10,
        "auto_enable_recommendations": False,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put("/api/settings/observer", json=body)
    assert resp.status_code == 422


# PUT с interval_seconds=700 (больше максимума 600) → 422.
@pytest.mark.asyncio
async def test_put_observer_settings_validates_interval_max(
    pg_engine, fake_redis_client, clean_observer_config
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    body = {
        "is_scanning_enabled": True,
        "default_interval_seconds": 700,
        "auto_enable_recommendations": False,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put("/api/settings/observer", json=body)
    assert resp.status_code == 422


# PATCH /scanning меняет только is_scanning_enabled.
@pytest.mark.asyncio
async def test_patch_scanning_changes_only_scanning_flag(
    pg_engine, fake_redis_client, clean_observer_config
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Сначала убеждаемся что scanning по умолчанию выключен (scanning OFF by default).
        get_before = await ac.get("/api/settings/observer")
        assert get_before.json()["is_scanning_enabled"] is False

        # Отключаем.
        patch_resp = await ac.patch("/api/settings/observer/scanning", json={"enabled": False})
        assert patch_resp.status_code == 200
        assert patch_resp.json()["is_scanning_enabled"] is False

        # Проверяем что get отражает изменение, остальные поля — нетронуты.
        get_after = await ac.get("/api/settings/observer")
    data_after = get_after.json()
    assert data_after["is_scanning_enabled"] is False
    # auto_enable_recommendations не должно измениться.
    assert data_after["auto_enable_recommendations"] is False


# Гейт включения: нельзя включить скан, когда мониторить нечего → 409 с причиной, флаг off.
@pytest.mark.asyncio
async def test_patch_scanning_enable_blocked_when_nothing_monitored(
    pg_engine, fake_redis_client, clean_observer_config, monkeypatch
):
    import core.observer.accounts as acc

    async def _fake_reason(_engine, _campaign_ids):
        return "Список кампаний пуст — выберите кампании для мониторинга на странице «Кампании»."

    monkeypatch.setattr(acc, "scan_nothing_monitored_reason", _fake_reason)
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch("/api/settings/observer/scanning", json={"enabled": True})
        assert resp.status_code == 409
        assert "кампани" in resp.json()["message"].lower()
        # Флаг НЕ включился — скан остался off.
        get_after = await ac.get("/api/settings/observer")
    assert get_after.json()["is_scanning_enabled"] is False


# Гейт пропускает включение, когда есть что мониторить (причина None) → 200, флаг on.
@pytest.mark.asyncio
async def test_patch_scanning_enable_allowed_when_monitored(
    pg_engine, fake_redis_client, clean_observer_config, monkeypatch
):
    import core.observer.accounts as acc

    async def _fake_none(_engine, _campaign_ids):
        return None

    monkeypatch.setattr(acc, "scan_nothing_monitored_reason", _fake_none)
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch("/api/settings/observer/scanning", json={"enabled": True})
        assert resp.status_code == 200
        assert resp.json()["is_scanning_enabled"] is True


# PATCH /auto-enable меняет baseline-колонку auto_enable_recommendations.
@pytest.mark.asyncio
async def test_patch_auto_enable_toggles_column(
    pg_engine, fake_redis_client, clean_observer_config
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Включаем auto-enable.
        resp_on = await ac.patch("/api/settings/observer/auto-enable", json={"enabled": True})
        assert resp_on.status_code == 200
        assert resp_on.json()["auto_enable_recommendations"] is True

        # Выключаем.
        resp_off = await ac.patch("/api/settings/observer/auto-enable", json={"enabled": False})
        assert resp_off.status_code == 200
        assert resp_off.json()["auto_enable_recommendations"] is False

        # GET должен видеть последнее значение.
        get_resp = await ac.get("/api/settings/observer")
    assert get_resp.json()["auto_enable_recommendations"] is False


# POST /scan-now создаёт durable interactive task без Redis control-path.
@pytest.mark.asyncio
async def test_scan_now_enqueues_durable_interactive_task(
    pg_engine, fake_redis_client, clean_observer_config
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/settings/observer/scan-now")
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert body["task_id"] > 0
    assert body["correlation_id"]

    async with pg_engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT task_type, status, lane, priority, requested_by, payload,
                           EXTRACT(EPOCH FROM (deadline_at - created_at)) AS deadline_seconds
                    FROM task_queue WHERE id = :task_id
                    """
                ),
                {"task_id": body["task_id"]},
            )
        ).one()
        assert row.task_type == "observer_scan"
        assert row.status == "pending"
        assert row.lane == "interactive"
        assert row.priority == 75
        assert row.requested_by == "operator_api"
        assert row.payload == {"reason": "operator_scan_now"}
        assert 119 <= float(row.deadline_seconds) <= 121
        await conn.execute(
            text("DELETE FROM task_queue WHERE id = :task_id"), {"task_id": body["task_id"]}
        )


@pytest.mark.asyncio
async def test_scan_now_does_not_depend_on_redis(pg_engine, clean_observer_config):
    from unittest.mock import AsyncMock, MagicMock

    broken_redis = MagicMock()
    broken_redis.publish = AsyncMock(side_effect=RuntimeError("Redis недоступен"))

    app = _make_app(engine=pg_engine, redis=broken_redis)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/settings/observer/scan-now")
    assert resp.status_code == 202
    task_id = resp.json()["task_id"]
    broken_redis.publish.assert_not_awaited()
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM task_queue WHERE id = :task_id"), {"task_id": task_id})


# GET отдаёт пустой campaign_ids по умолчанию; scan_source выпилен (am_tabular — единственный).
@pytest.mark.asyncio
async def test_get_returns_campaign_ids_default(
    pg_engine, fake_redis_client, clean_observer_config
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/settings/observer")
    data = resp.json()
    assert "scan_source" not in data  # поле выпилено вместе с DOM-сканером
    assert data["campaign_ids"] == []


# PATCH /campaigns задаёт allowlist кампаний для am-режима (#3).
@pytest.mark.asyncio
async def test_patch_campaigns_sets_allowlist(pg_engine, fake_redis_client, clean_observer_config):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.patch(
            "/api/settings/observer/campaigns",
            json={"campaign_ids": ["120244801453970044", "120244530626090044"]},
        )
        assert r.status_code == 200
        assert r.json()["campaign_ids"] == ["120244801453970044", "120244530626090044"]
        g = await ac.get("/api/settings/observer")
        assert len(g.json()["campaign_ids"]) == 2


@pytest.mark.asyncio
async def test_refresh_campaigns_requires_postgresql_vision_credentials(
    pg_engine,
    fake_redis_client,
    clean_observer_config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import patch

    monkeypatch.setenv("VISION_X_TOKEN", "must-not-be-used")
    monkeypatch.setenv("VISION_PROFILE_ID", "must-not-be-used")
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM vision_config"))

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    with patch("clients.python_grpc.client.BrowserAgentClient") as client_ctor:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/api/settings/observer/campaigns/refresh",
                params={"ad_account_id": "123"},
            )

    assert response.status_code == 409
    assert response.json()["message"] == "Vision runtime не настроен"
    client_ctor.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_campaigns_is_blocked_by_browser_maintenance(
    pg_engine,
    fake_redis_client,
    clean_observer_config,
) -> None:
    import uuid
    from unittest.mock import patch

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
        app = _make_app(engine=pg_engine, redis=fake_redis_client)
        with patch("clients.python_grpc.client.BrowserAgentClient") as client_ctor:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as ac:
                response = await ac.post(
                    "/api/settings/observer/campaigns/refresh",
                    params={"ad_account_id": "123"},
                )
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM system_config WHERE key = 'browser_maintenance'"))

    assert response.status_code == 409
    client_ctor.assert_not_called()


# PUT с is_scanning_enabled=true проходит гейт «нечего сканировать» → 409 (аудит 2026-07-12, C-1).
@pytest.mark.asyncio
async def test_put_gates_scanning_enable_when_nothing_monitored(
    pg_engine, fake_redis_client, clean_observer_config, monkeypatch
):
    async def _always_blocked(engine, campaign_ids):
        return "Нет активных офферов с кабинетами — сканировать нечего."

    monkeypatch.setattr("core.observer.accounts.scan_nothing_monitored_reason", _always_blocked)
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    body = {
        "is_scanning_enabled": True,
        "default_interval_seconds": 60,
        "auto_enable_recommendations": False,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put("/api/settings/observer", json=body)
        assert resp.status_code == 409
        # Флаг НЕ включился — GET отражает прежнее состояние.
        g = await ac.get("/api/settings/observer")
        assert g.json()["is_scanning_enabled"] is False


# PUT с is_scanning_enabled=false гейт не зовёт — выключение всегда разрешено (C-1).
@pytest.mark.asyncio
async def test_put_disable_scanning_skips_gate(
    pg_engine, fake_redis_client, clean_observer_config, monkeypatch
):
    async def _always_blocked(engine, campaign_ids):
        return "Нет активных офферов с кабинетами — сканировать нечего."

    monkeypatch.setattr("core.observer.accounts.scan_nothing_monitored_reason", _always_blocked)
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    body = {
        "is_scanning_enabled": False,
        "default_interval_seconds": 60,
        "auto_enable_recommendations": False,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put("/api/settings/observer", json=body)
        assert resp.status_code == 200


# PATCH /owner-tag меняет ТОЛЬКО тег, остальные поля не трогает (анти лост-апдейт, C-1).
@pytest.mark.asyncio
async def test_patch_owner_tag_touches_only_tag(
    pg_engine, fake_redis_client, clean_observer_config
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Подготовка: известное состояние остальных полей через PUT (скан выключен).
        await ac.put(
            "/api/settings/observer",
            json={
                "is_scanning_enabled": False,
                "default_interval_seconds": 120,
                "auto_enable_recommendations": True,
            },
        )
        r = await ac.patch(
            "/api/settings/observer/owner-tag", json={"owner_campaign_tag": "MV,ABC"}
        )
        assert r.status_code == 200
        data = r.json()
        assert data["owner_campaign_tag"] == "MV,ABC"
        # Остальные поля не изменились.
        assert data["is_scanning_enabled"] is False
        assert data["default_interval_seconds"] == 120
        assert data["auto_enable_recommendations"] is True


# PATCH /owner-tag с пустой строкой нормализуется в null (фильтр выключен).
@pytest.mark.asyncio
async def test_patch_owner_tag_empty_string_becomes_null(
    pg_engine, fake_redis_client, clean_observer_config
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.patch("/api/settings/observer/owner-tag", json={"owner_campaign_tag": "  "})
        assert r.status_code == 200
        assert r.json()["owner_campaign_tag"] is None

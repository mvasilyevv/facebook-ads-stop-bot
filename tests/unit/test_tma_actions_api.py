# -*- coding: utf-8 -*-
"""Unit-тесты для эндпоинтов действий над объявлениями в роутере /api/tma."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.deps import require_tma_session

FAKE_API_KEY = "test_api_key_actions"
FAKE_USER_ID = "42"

# Заглушка AdDetailDTO
FAKE_DTO = MagicMock()
FAKE_DTO.__dict__ = {
    "fb_ad_id": "abc",
    "ad_name": "Test Ad",
    "campaign_name": "Test Campaign",
    "adset_name": "Test Adset",
    "state": "NORMAL",
    "account_id": "act_123",
    "metrics": {"spend": 100.0},
    "snooze_until": None,
    "recent_alerts": [],
    "can_open_in_ads_manager": True,
}


def _make_app() -> FastAPI:
    """Создаёт изолированное FastAPI-приложение с TMA-роутером."""
    settings_mock = MagicMock()
    settings_mock.api_key = FAKE_API_KEY
    settings_mock.tma_session_ttl_seconds = 3600
    settings_mock.telegram_bot_token = "fake_token"

    mini = FastAPI()
    with (
        patch("apps.api.routers.tma.get_settings", return_value=settings_mock),
        patch("apps.api.deps.get_settings", return_value=settings_mock),
    ):
        from apps.api.routers import tma as tma_router

        mini.include_router(tma_router.router)

    return mini


async def _fake_session(request: Request) -> None:
    """Подставляет tma_user_id в state (мок авторизации)."""
    request.state.tma_user_id = FAKE_USER_ID


@pytest.fixture()
def app():
    """FastAPI-приложение с переопределённой TMA-зависимостью."""
    a = _make_app()
    a.dependency_overrides[require_tma_session] = _fake_session
    return a


@pytest.fixture()
def client(app):
    """TestClient с активной TMA-сессией."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def client_no_auth():
    """TestClient без переопределения зависимости (проверка 401)."""
    a = _make_app()
    with TestClient(a, raise_server_exceptions=False) as c:
        yield c


# GET /api/tma/ads/abc → 200, все поля DTO присутствуют
def test_get_ad_detail_200(client):
    with patch("core.ads.actions.get_ad_detail", new=AsyncMock(return_value=FAKE_DTO)):
        resp = client.get("/api/tma/ads/abc")
    assert resp.status_code == 200
    data = resp.json()
    assert data["fb_ad_id"] == "abc"
    assert data["state"] == "NORMAL"
    assert data["metrics"] == {"spend": 100.0}
    assert data["can_open_in_ads_manager"] is True


# GET /api/tma/ads/missing → 404 при AdNotFoundError
def test_get_ad_detail_404(client):
    from core.ads.actions import AdNotFoundError

    with patch(
        "core.ads.actions.get_ad_detail", new=AsyncMock(side_effect=AdNotFoundError("missing"))
    ):
        resp = client.get("/api/tma/ads/missing")
    assert resp.status_code == 404


# POST .../disable → 200, возвращает task_id
def test_disable_ad_200(client):
    result = {"task_id": "task-uuid-1", "created_new": True, "ad_name": "Test Ad"}
    with patch("core.ads.actions.disable_ad", new=AsyncMock(return_value=result)):
        resp = client.post("/api/tma/ads/abc/disable", json={"reason": "overspend"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["task_id"] == "task-uuid-1"
    assert data["created_new"] is True


# POST .../snooze {minutes: 30} → 200, snoozed_until в ответе
def test_snooze_ad_200(client):
    until = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
    with patch("core.ads.actions.snooze_ad", new=AsyncMock(return_value=until)):
        resp = client.post("/api/tma/ads/abc/snooze", json={"minutes": 30})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "snoozed_until" in data
    assert "2025-01-01" in data["snoozed_until"]


# POST .../snooze {minutes: 5000} → 422 (Pydantic-валидация, le=720)
def test_snooze_ad_422_too_many_minutes(client):
    resp = client.post("/api/tma/ads/abc/snooze", json={"minutes": 5000})
    assert resp.status_code == 422


# POST .../claim без активного алерта → 409 (AdActionError)
def test_claim_ad_409(client):
    from core.ads.actions import AdActionError

    with patch(
        "core.ads.actions.claim_ad", new=AsyncMock(side_effect=AdActionError("no active alert"))
    ):
        resp = client.post("/api/tma/ads/abc/claim")
    assert resp.status_code == 409


# Без TMA-сессии (без override) → 401
def test_get_ad_no_auth_401(client_no_auth):
    resp = client_no_auth.get("/api/tma/ads/abc")
    assert resp.status_code == 401

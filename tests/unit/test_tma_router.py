# -*- coding: utf-8 -*-
"""Unit-тесты для роутера /api/tma (аутентификация Telegram Mini App)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth.tma import issue_session_token

FAKE_BOT_TOKEN = "fake_router_token_99"
FAKE_API_KEY = "fake_api_key_for_tma_tests"
FAKE_OWNER_ID = "111111"


def _build_init_data(user_id: int, bot_token: str, auth_date: int | None = None) -> str:
    """Строит валидный initData Telegram WebApp для теста."""
    if auth_date is None:
        auth_date = int(time.time())
    user = json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":"))
    fields = {"auth_date": str(auth_date), "user": user}
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


def _make_settings():
    """Создаёт заглушку настроек для тестов."""
    s = MagicMock()
    s.telegram_bot_token = FAKE_BOT_TOKEN
    s.api_key = FAKE_API_KEY
    s.tma_session_ttl_seconds = 3600
    return s


@pytest.fixture()
def tma_app():
    """Минимальное FastAPI-приложение только с TMA-роутером."""
    settings_mock = _make_settings()

    # Создаём изолированное приложение, не касаясь Sentry
    mini = FastAPI()

    with (
        patch("apps.api.routers.tma.get_settings", return_value=settings_mock),
        patch("apps.api.deps.get_settings", return_value=settings_mock),
    ):
        from apps.api.routers import tma as tma_router

        mini.include_router(tma_router.router)
        yield mini, settings_mock


@pytest.fixture()
def client(tma_app):
    """TestClient для изолированного TMA-приложения."""
    app, _ = tma_app
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# Проверяем, что /api/tma/auth возвращает 401 при неверном initData
def test_auth_endpoint_401_for_invalid_init_data(client):
    resp = client.post("/api/tma/auth", json={"init_data": "invalid_data"})
    assert resp.status_code == 401


# Проверяем, что /api/tma/auth с пустым initData возвращает 401
def test_auth_endpoint_401_for_empty_init_data(client):
    resp = client.post("/api/tma/auth", json={"init_data": ""})
    assert resp.status_code == 401


# Проверяем, что /api/tma/me принимает Bearer-токен и возвращает telegram_user_id
def test_protected_endpoint_accepts_bearer(tma_app):
    app, settings_mock = tma_app
    with (
        patch("apps.api.deps.get_settings", return_value=settings_mock),
        patch("apps.api.routers.tma.get_settings", return_value=settings_mock),
    ):
        with TestClient(app, raise_server_exceptions=True) as c:
            token = issue_session_token("777", 3600, FAKE_API_KEY)
            resp = c.get("/api/tma/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()["telegram_user_id"] == "777"

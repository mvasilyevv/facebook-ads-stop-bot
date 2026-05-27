# -*- coding: utf-8 -*-
"""Интеграционный: POST /api/v1/postback/adsetpro — auth и парсинг body.

Тесты используют sync TestClient. Секрет переопределяется через
`app.dependency_overrides[get_settings]` — это локально для каждого app
и не трогает глобальный синглтон Settings.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.deps import get_settings
from apps.api.main import create_app
from core.config import Settings

_VALID_BODY = {
    "click_id": "abc-123",
    "fb_ad_id": "ad-1",
    "event_type": "ftd",
    "revenue": "10.50",
    "currency": "USD",
}


def _make_app_with_secret(secret: str) -> object:
    """Собрать FastAPI с заданным значением adsetpro_postback_secret."""
    app = create_app()
    settings_override = Settings(adsetpro_postback_secret=secret)
    app.dependency_overrides[get_settings] = lambda: settings_override
    return app


# Если секрет в env пустой — endpoint считается не настроенным, возвращает 503.
def test_postback_returns_503_when_secret_not_configured() -> None:
    app = _make_app_with_secret("")
    client = TestClient(app)
    resp = client.post(
        "/api/v1/postback/adsetpro",
        json=_VALID_BODY,
        headers={"X-Postback-Secret": "any"},
    )
    assert resp.status_code == 503
    assert "not configured" in resp.json()["detail"].lower()


# Секрет в header не совпадает с env → 401, body не парсится.
def test_postback_returns_401_on_wrong_secret() -> None:
    app = _make_app_with_secret("real-secret")
    client = TestClient(app)
    resp = client.post(
        "/api/v1/postback/adsetpro",
        json=_VALID_BODY,
        headers={"X-Postback-Secret": "wrong"},
    )
    assert resp.status_code == 401


# Если header отсутствует — тоже 401 (не 422), чтобы не палить наличие секрета.
def test_postback_returns_401_when_header_missing() -> None:
    app = _make_app_with_secret("real-secret")
    client = TestClient(app)
    resp = client.post("/api/v1/postback/adsetpro", json=_VALID_BODY)
    assert resp.status_code == 401


# Валидный секрет + корректный body → 202 и эхо click_id в ответе.
def test_postback_accepts_with_valid_secret() -> None:
    app = _make_app_with_secret("real-secret")
    client = TestClient(app)
    resp = client.post(
        "/api/v1/postback/adsetpro",
        json=_VALID_BODY,
        headers={"X-Postback-Secret": "real-secret"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["received"] is True
    assert body["click_id"] == "abc-123"


# Битый body (нет обязательного click_id) → 422 от pydantic-валидатора.
def test_postback_returns_422_on_invalid_body() -> None:
    app = _make_app_with_secret("real-secret")
    client = TestClient(app)
    resp = client.post(
        "/api/v1/postback/adsetpro",
        json={"event_type": "ftd"},  # нет click_id
        headers={"X-Postback-Secret": "real-secret"},
    )
    assert resp.status_code == 422


# Лишние поля в body не должны ломать parsing — попадают в PostbackEvent.raw.
def test_postback_accepts_extra_fields() -> None:
    app = _make_app_with_secret("real-secret")
    client = TestClient(app)
    payload = dict(_VALID_BODY)
    payload["custom_field"] = "anything"
    resp = client.post(
        "/api/v1/postback/adsetpro",
        json=payload,
        headers={"X-Postback-Secret": "real-secret"},
    )
    assert resp.status_code == 202

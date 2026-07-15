# -*- coding: utf-8 -*-
"""Интеграционный: POST /api/v1/postback/adsetpro — auth и парсинг body.

Тесты используют sync TestClient. Секрет переопределяется через
`app.dependency_overrides[get_settings]` — это локально для каждого app
и не трогает глобальный синглтон Settings.

После Волны 3 endpoint делает реальный INSERT через ingest_postback. В sync-тестах
заменяем core.adset_pro.ingest.ingest_postback на стаб, чтобы не зависеть от БД
(integration с БД — отдельные тесты в test_adset_pro_ingest.py).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.deps import get_settings
from apps.api.main import create_app
from core.adset_pro.ingest import IngestResult
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


@pytest.fixture(autouse=True)
def _stub_ingest(monkeypatch):
    """Подменяем ingest_postback на стаб — sync TestClient не работает с реальным БД-engine.

    Возвращаем фиктивный IngestResult с event_id=1, чтобы роутер вернул осмысленный
    202-ответ. Реальный ingest проверяется в test_adset_pro_ingest.py.
    """

    async def _fake_ingest(_engine, _event, *, signature_valid=True):
        return IngestResult(
            inserted=True,
            is_duplicate=False,
            event_id=1,
            fb_ad_fk=None,
        )

    # resolve_adsetpro_postback_secret делает DB-чтение (adsetpro_credentials) — стабим
    # на возврат fallback (== env), чтобы sync TestClient не трогал реальный БД-engine.
    async def _fake_resolve_secret(_engine, *, fallback=None):
        return fallback or ""

    monkeypatch.setattr("apps.api.routers.postback.ingest_postback", _fake_ingest)
    monkeypatch.setattr(
        "apps.api.routers.postback.resolve_adsetpro_postback_secret", _fake_resolve_secret
    )
    yield


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


def test_get_postback_accepts_query_token_contract() -> None:
    app = _make_app_with_secret("real-secret")
    client = TestClient(app)
    resp = client.get(
        "/api/v1/postback/adsetpro",
        params={
            "token": "real-secret",
            "click_id": "get-click-1",
            "event_type": "registration",
            "sub8": "238000000001",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
    assert resp.json()["click_id"] == "get-click-1"


@pytest.mark.parametrize(
    ("provider_status", "canonical"),
    [
        ("hold", "registration"),
        ("CPA_HOLD", "registration"),
        ("accept", "ftd"),
        ("CPA_ACCEPT", "ftd"),
        ("redep", "redeposit"),
        ("CPA_REDEP", "redeposit"),
    ],
)
def test_get_postback_accepts_provider_status_aliases(
    monkeypatch, provider_status: str, canonical: str
) -> None:
    captured = {}

    async def _capture(_engine, event, *, signature_valid=True):
        captured["event_type"] = event.event_type
        return IngestResult(inserted=True, is_duplicate=False, event_id=1, fb_ad_fk=None)

    monkeypatch.setattr("apps.api.routers.postback.ingest_postback", _capture)
    params = {
        "token": "real-secret",
        "click_id": f"alias-{provider_status}",
        "status": provider_status,
    }
    if canonical == "redeposit":
        params["provider_event_id"] = f"tx-{provider_status}"
    response = TestClient(_make_app_with_secret("real-secret")).get(
        "/api/v1/postback/adsetpro", params=params
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert captured["event_type"] == canonical


def test_get_secret_is_removed_before_persistence(monkeypatch) -> None:
    captured = {}

    async def _capture(_engine, event, *, signature_valid=True):
        captured["raw"] = event.raw
        return IngestResult(
            inserted=True,
            is_duplicate=False,
            event_id=1,
            fb_ad_fk=None,
        )

    monkeypatch.setattr("apps.api.routers.postback.ingest_postback", _capture)
    app = _make_app_with_secret("real-secret")
    response = TestClient(app).get(
        "/api/v1/postback/adsetpro",
        params={
            "token": "real-secret",
            "click_id": "click-secret-redaction",
            "event_type": "registration",
        },
    )

    assert response.status_code == 200
    assert "token" not in captured["raw"]
    assert "real-secret" not in repr(captured["raw"])


def test_get_postback_survives_redis_outage() -> None:
    class BrokenRedis:
        async def incr(self, *_args, **_kwargs):
            raise ConnectionError("redis down")

        async def publish(self, *_args, **_kwargs):
            raise ConnectionError("redis down")

    app = _make_app_with_secret("real-secret")
    app.state.redis = BrokenRedis()
    response = TestClient(app).get(
        "/api/v1/postback/adsetpro",
        params={
            "token": "real-secret",
            "click_id": "click-no-redis",
            "event_type": "registration",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


def test_get_postback_rejects_wrong_query_token() -> None:
    app = _make_app_with_secret("real-secret")
    client = TestClient(app)
    resp = client.get(
        "/api/v1/postback/adsetpro",
        params={"token": "wrong", "click_id": "x", "event_type": "ftd"},
    )
    assert resp.status_code == 401


@pytest.mark.parametrize("event_type", ["decline", "rejected", "trash", "baddep", "unknown"])
def test_unsupported_or_negative_status_is_200_ignored(monkeypatch, event_type: str) -> None:
    async def _must_not_ingest(*_args, **_kwargs):
        raise AssertionError("ignored status must not be stored")

    monkeypatch.setattr("apps.api.routers.postback.ingest_postback", _must_not_ingest)
    app = _make_app_with_secret("real-secret")
    client = TestClient(app)
    resp = client.post(
        "/api/v1/postback/adsetpro",
        json={"event_type": event_type},
        headers={"X-Postback-Secret": "real-secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_redeposit_without_provider_event_id_is_200_ignored(monkeypatch) -> None:
    async def _must_not_ingest(*_args, **_kwargs):
        raise AssertionError("unidentified redeposit must not be stored")

    monkeypatch.setattr("apps.api.routers.postback.ingest_postback", _must_not_ingest)
    app = _make_app_with_secret("real-secret")
    client = TestClient(app)
    resp = client.post(
        "/api/v1/postback/adsetpro",
        json={"click_id": "r-1", "event_type": "redeposit"},
        headers={"X-Postback-Secret": "real-secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["reason"] == "redeposit_without_provider_event_id"

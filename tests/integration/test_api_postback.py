# -*- coding: utf-8 -*-
"""Canonical GET AdSet.pro callback contracts."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from core.adset_pro.ingest import IngestResult


@pytest.fixture(autouse=True)
def _stub_ingest_and_secret(monkeypatch):
    async def _fake_ingest(_engine, _event, *, signature_valid=True):
        return IngestResult(inserted=True, is_duplicate=False, event_id=1, fb_ad_fk=None)

    async def _secret(_engine):
        return "real-secret"

    monkeypatch.setattr("apps.api.routers.postback.ingest_postback", _fake_ingest)
    monkeypatch.setattr("apps.api.routers.postback.resolve_adsetpro_postback_secret", _secret)


def test_get_postback_accepts_query_token_contract() -> None:
    response = TestClient(create_app()).get(
        "/api/v1/postback/adsetpro",
        params={
            "token": "real-secret",
            "click_id": "get-click-1",
            "event_type": "registration",
            "sub8": "238000000001",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


def test_post_transport_is_not_registered() -> None:
    app = create_app()
    assert "post" not in app.openapi()["paths"]["/api/v1/postback/adsetpro"]
    assert TestClient(app).post("/api/v1/postback/adsetpro", json={}).status_code == 405


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
def test_get_postback_canonicalizes_provider_aliases(
    monkeypatch, provider_status, canonical
) -> None:
    captured = {}

    async def _capture(_engine, event, *, signature_valid=True):
        captured["event_type"] = event.event_type
        return IngestResult(inserted=True, is_duplicate=False, event_id=1, fb_ad_fk=None)

    monkeypatch.setattr("apps.api.routers.postback.ingest_postback", _capture)
    params = {"token": "real-secret", "click_id": "alias", "status": provider_status}
    if canonical == "redeposit":
        params["provider_event_id"] = "tx-alias"
    response = TestClient(create_app()).get("/api/v1/postback/adsetpro", params=params)

    assert response.status_code == 200
    assert captured["event_type"] == canonical


def test_postback_rejects_missing_or_wrong_database_secret(monkeypatch) -> None:
    async def _empty(_engine):
        return ""

    monkeypatch.setattr("apps.api.routers.postback.resolve_adsetpro_postback_secret", _empty)
    response = TestClient(create_app()).get(
        "/api/v1/postback/adsetpro", params={"token": "any", "click_id": "x", "event_type": "ftd"}
    )
    assert response.status_code == 503


def test_postback_secret_is_not_persisted(monkeypatch) -> None:
    captured = {}

    async def _capture(_engine, event, *, signature_valid=True):
        captured["raw"] = event.raw
        return IngestResult(inserted=True, is_duplicate=False, event_id=1, fb_ad_fk=None)

    monkeypatch.setattr("apps.api.routers.postback.ingest_postback", _capture)
    response = TestClient(create_app()).get(
        "/api/v1/postback/adsetpro",
        params={"token": "real-secret", "click_id": "x", "event_type": "registration"},
    )
    assert response.status_code == 200
    assert "token" not in captured["raw"]


@pytest.mark.parametrize(
    ("revenue_params", "expected"),
    [
        ({}, None),
        ({"revenue": ""}, None),
        ({"revenue": "   "}, None),
        ({"revenue": "0"}, Decimal("0")),
    ],
)
def test_postback_distinguishes_unknown_revenue_from_known_zero(
    monkeypatch,
    revenue_params,
    expected,
) -> None:
    captured = {}

    async def _capture(_engine, event, *, signature_valid=True):
        captured["revenue"] = event.revenue
        return IngestResult(inserted=True, is_duplicate=False, event_id=1, fb_ad_fk=None)

    monkeypatch.setattr("apps.api.routers.postback.ingest_postback", _capture)
    response = TestClient(create_app()).get(
        "/api/v1/postback/adsetpro",
        params={
            "token": "real-secret",
            "click_id": "revenue-contract",
            "event_type": "ftd",
            **revenue_params,
        },
    )

    assert response.status_code == 200
    assert captured["revenue"] == expected


@pytest.mark.parametrize("revenue", ["not-a-number", "NaN", "Infinity", "-Infinity"])
def test_postback_rejects_malformed_or_non_finite_revenue(monkeypatch, revenue) -> None:
    ingest = AsyncMock()
    monkeypatch.setattr("apps.api.routers.postback.ingest_postback", ingest)

    response = TestClient(create_app()).get(
        "/api/v1/postback/adsetpro",
        params={
            "token": "real-secret",
            "click_id": "invalid-revenue",
            "event_type": "ftd",
            "revenue": revenue,
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "unprocessable_entity"
    assert response.json()["message"] == "invalid revenue"
    ingest.assert_not_awaited()

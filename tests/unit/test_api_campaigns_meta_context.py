# -*- coding: utf-8 -*-
"""Unit contract for durable campaign account context."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import apps.api.routers.v1.campaigns_meta as mod
from apps.api.deps import get_engine
from apps.api.routers.v1.campaigns_meta import router
from core.campaign_builder.account_context import (
    CampaignAccountContext,
    normalize_campaign_account_id,
)


def _client_for(monkeypatch: pytest.MonkeyPatch, context: CampaignAccountContext) -> TestClient:
    async def _resolve(_engine, *, account_id: str) -> CampaignAccountContext:
        assert account_id == "act_123"
        return context

    monkeypatch.setattr(mod, "resolve_campaign_account_context", _resolve)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_engine] = lambda: object()
    return TestClient(app, raise_server_exceptions=True)


def test_ready_context_returns_immutable_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    observed_at = datetime(2026, 7, 29, 8, 30, tzinfo=UTC)
    client = _client_for(
        monkeypatch,
        CampaignAccountContext(
            account_id="123",
            state="ready",
            timezone_name="America/New_York",
            currency="USD",
            currency_exponent=2,
            observed_at=observed_at,
            next_start_date=date(2026, 7, 30),
            issue=None,
        ),
    )

    response = client.get("/api/campaigns/ad-account-context", params={"act_id": "act_123"})

    assert response.status_code == 200
    assert response.json() == {
        "account_id": "123",
        "state": "ready",
        "timezone_name": "America/New_York",
        "currency": "USD",
        "currency_exponent": 2,
        "observed_at": "2026-07-29T08:30:00Z",
        "next_start_date": "2026-07-30",
        "issue": None,
    }


@pytest.mark.parametrize(
    ("state", "issue"),
    [
        ("stale", "account_context_stale"),
        ("unavailable", "account_context_missing"),
    ],
)
def test_non_ready_context_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    issue: str,
) -> None:
    client = _client_for(
        monkeypatch,
        CampaignAccountContext(
            account_id="123",
            state=state,  # type: ignore[arg-type]
            timezone_name=None,
            currency=None,
            currency_exponent=None,
            observed_at=None,
            next_start_date=None,
            issue=issue,
        ),
    )

    response = client.get("/api/campaigns/ad-account-context", params={"act_id": "act_123"})

    assert response.status_code == 200
    assert response.json()["state"] == state
    assert response.json()["issue"] == issue
    assert response.json()["next_start_date"] is None


def test_invalid_account_id_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _resolve(_engine, *, account_id: str) -> CampaignAccountContext:
        raise ValueError(account_id)

    monkeypatch.setattr(mod, "resolve_campaign_account_context", _resolve)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_engine] = lambda: object()
    client = TestClient(app, raise_server_exceptions=True)

    response = client.get("/api/campaigns/ad-account-context", params={"act_id": "act_bad"})

    assert response.status_code == 422


def test_removed_numeric_offset_route_is_not_registered() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app, raise_server_exceptions=True)

    response = client.get("/api/campaigns/ad-account-timezone", params={"act_id": "123"})

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("act_123", "123"),
        ("123", "123"),
        ("  act_456  ", "456"),
    ],
)
def test_normalize_campaign_account_id(raw: str, expected: str) -> None:
    assert normalize_campaign_account_id(raw) == expected


@pytest.mark.parametrize("raw", ["", "act_", "abc", "12-3"])
def test_normalize_campaign_account_id_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_campaign_account_id(raw)

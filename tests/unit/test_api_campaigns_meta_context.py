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
    CAMPAIGN_ACCOUNT_CONTEXT_STALE,
    CAMPAIGN_ACCOUNT_CONTEXT_UNAVAILABLE,
    CAMPAIGN_AD_ACCOUNT_NOT_ACTIVE,
    CampaignAccountContext,
    normalize_campaign_account_id,
)


def _client_for(
    monkeypatch: pytest.MonkeyPatch,
    context: CampaignAccountContext,
    *,
    refreshed: CampaignAccountContext | None = None,
    refresh_issue: str | None = None,
    refresh_calls: list[str] | None = None,
) -> TestClient:
    """Тестовый клиент ручки контекста.

    ``refreshed`` — что вернёт ПОВТОРНОЕ чтение после живого подтягивания.
    Без него повторное чтение отдаёт тот же контекст, что и первое.
    """
    reads: list[CampaignAccountContext] = [context]
    if refreshed is not None:
        reads.append(refreshed)

    async def _resolve(_engine, *, account_id: str) -> CampaignAccountContext:
        assert account_id in {"act_123", "123"}
        return reads.pop(0) if len(reads) > 1 else reads[0]

    async def _refresh(_engine, numeric_act_id: str) -> str | None:
        if refresh_calls is not None:
            refresh_calls.append(numeric_act_id)
        return refresh_issue

    monkeypatch.setattr(mod, "resolve_campaign_account_context", _resolve)
    monkeypatch.setattr(mod, "_refresh_account_context_once", _refresh)
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
        ("stale", CAMPAIGN_ACCOUNT_CONTEXT_STALE),
        ("unavailable", CAMPAIGN_ACCOUNT_CONTEXT_UNAVAILABLE),
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
    payload = response.json()
    assert payload["state"] == state
    # Оператору уходит причина словами, а не машинный код.
    assert payload["issue"] and payload["issue"] != issue
    assert payload["next_start_date"] is None


# Отключённый Meta кабинет не может выглядеть готовым: 20.08.2026 ручка отдавала
# ready/issue=null, а первый же POST залива получал отказ Meta.
def test_disabled_account_is_unavailable_with_operator_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disabled = CampaignAccountContext(
        account_id="123",
        state="unavailable",
        timezone_name="America/New_York",
        currency="USD",
        currency_exponent=None,
        observed_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        next_start_date=None,
        issue=CAMPAIGN_AD_ACCOUNT_NOT_ACTIVE,
        account_status=2,
    )
    client = _client_for(monkeypatch, disabled, refreshed=disabled)

    payload = client.get("/api/campaigns/ad-account-context", params={"act_id": "act_123"}).json()

    assert payload["state"] == "unavailable"
    assert "отключ" in payload["issue"].lower()
    assert payload["next_start_date"] is None


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


# Готовый снимок — живое чтение не нужно: лишний поход в Meta на каждый показ
# шага визарда дорог и ничего не уточняет.
def test_ready_context_never_touches_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    refresh_calls: list[str] = []
    client = _client_for(
        monkeypatch,
        CampaignAccountContext(
            account_id="123",
            state="ready",
            timezone_name="America/New_York",
            currency="USD",
            currency_exponent=2,
            observed_at=datetime(2026, 8, 17, 8, 30, tzinfo=UTC),
            next_start_date=date(2026, 8, 18),
            issue=None,
        ),
        refresh_calls=refresh_calls,
    )

    resp = client.get("/api/campaigns/ad-account-context", params={"act_id": "act_123"})

    assert resp.status_code == 200
    assert resp.json()["state"] == "ready"
    assert refresh_calls == []


# Снимка нет — ручка подтягивает его сама и отвечает ПЕРЕЧИТАННОЙ строкой.
def test_missing_context_is_fetched_and_reread(monkeypatch: pytest.MonkeyPatch) -> None:
    refresh_calls: list[str] = []
    client = _client_for(
        monkeypatch,
        CampaignAccountContext(
            account_id="123",
            state="unavailable",
            timezone_name=None,
            currency=None,
            currency_exponent=None,
            observed_at=None,
            next_start_date=None,
            issue=None,
        ),
        refreshed=CampaignAccountContext(
            account_id="123",
            state="ready",
            timezone_name="America/New_York",
            currency="USD",
            currency_exponent=2,
            observed_at=datetime(2026, 8, 17, 9, 0, tzinfo=UTC),
            next_start_date=date(2026, 8, 18),
            issue=None,
        ),
        refresh_calls=refresh_calls,
    )

    resp = client.get("/api/campaigns/ad-account-context", params={"act_id": "act_123"})

    assert resp.status_code == 200
    payload = resp.json()
    assert refresh_calls == ["123"]
    assert payload["state"] == "ready"
    assert payload["timezone_name"] == "America/New_York"
    assert payload["currency"] == "USD"
    assert payload["currency_exponent"] == 2
    assert payload["issue"] is None


# Живое чтение не удалось — это не 5xx, а состояние с внятной причиной.
def test_failed_refresh_becomes_a_readable_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    unavailable = CampaignAccountContext(
        account_id="123",
        state="unavailable",
        timezone_name=None,
        currency=None,
        currency_exponent=None,
        observed_at=None,
        next_start_date=None,
        issue=None,
    )
    client = _client_for(
        monkeypatch,
        unavailable,
        refreshed=unavailable,
        refresh_issue="Meta не отдала часовой пояс и валюту по кабинету",
    )

    resp = client.get("/api/campaigns/ad-account-context", params={"act_id": "act_123"})

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["state"] == "unavailable"
    assert payload["issue"] == "Meta не отдала часовой пояс и валюту по кабинету"


# Собственная причина из базы важнее причины неудачного подтягивания: она
# описывает сам снимок (например, неподдерживаемую валюту), а не поход в Meta.
def test_durable_issue_wins_over_refresh_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    stale = CampaignAccountContext(
        account_id="123",
        state="stale",
        timezone_name="America/New_York",
        currency="USD",
        currency_exponent=2,
        observed_at=datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
        next_start_date=None,
        issue=CAMPAIGN_ACCOUNT_CONTEXT_STALE,
    )
    client = _client_for(
        monkeypatch,
        stale,
        refreshed=stale,
        refresh_issue="Канал Meta недоступен — снимок кабинета не обновлён",
    )

    resp = client.get("/api/campaigns/ad-account-context", params={"act_id": "act_123"})

    assert "устарел" in resp.json()["issue"]
    assert "Канал Meta недоступен" not in resp.json()["issue"]

# -*- coding: utf-8 -*-
"""Unit: GET /api/campaigns/ad-account-timezone — автоподхват TZ кабинета.

Проверяем парсинг timezone_offset_hours_utc (включая отрицательный), формирование
tz_offset_str (±HH:00) и маппинг ошибок: 503 (Vision/circuit/grpc недоступны),
422 (доменная ошибка Meta / кабинет без оффсета), 400 (пустой act_id).

Клиент Marketing API мокается целиком (execute_graph_call) — БЕЗ живой БД/Vision.
"""

from __future__ import annotations

from typing import Any

import grpc
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import apps.api.routers.v1.campaigns_meta as mod
from apps.api.deps import get_engine
from apps.api.routers.v1.campaigns_meta import (
    _normalize_act_id,
    _tz_offset_to_str,
    router,
)
from core.browser.circuit_breaker import CircuitOpenError
from core.meta_api.errors import (
    NotFoundError,
    RateLimitedError,
    SessionUnavailableError,
    TemporaryError,
)


class _FakeClient:
    """Подмена MetaApiClient: execute_graph_call возвращает заданный resp или бросает."""

    def __init__(self, *, resp: dict[str, Any] | None = None, raises: BaseException | None = None):
        self._resp = resp or {}
        self._raises = raises
        self.started = False
        self.closed = False
        self.last_kwargs: dict[str, Any] | None = None

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    async def execute_graph_call(self, **kwargs: Any) -> dict[str, Any]:
        self.last_kwargs = kwargs
        if self._raises is not None:
            raise self._raises
        return self._resp


def _client_for(monkeypatch, fake: _FakeClient) -> TestClient:
    """FastAPI-приложение только с этим роутером; engine-dep и client замоканы."""
    monkeypatch.setattr(mod, "_build_meta_client", lambda engine: fake)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_engine] = lambda: object()
    return TestClient(app, raise_server_exceptions=True)


# Положительный оффсет (+3) парсится и форматируется в "+03:00".
def test_positive_offset(monkeypatch) -> None:
    fake = _FakeClient(resp={"timezone_offset_hours_utc": 3, "timezone_name": "Europe/Moscow"})
    client = _client_for(monkeypatch, fake)
    r = client.get("/api/campaigns/ad-account-timezone", params={"act_id": "act_123"})
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "tz_offset_hours": 3,
        "tz_offset_str": "+03:00",
        "timezone_name": "Europe/Moscow",
    }
    assert fake.last_kwargs is not None
    assert fake.last_kwargs["ad_account_id"] == "act_123"
    # Канал закрыли в finally даже на успехе.
    assert fake.closed is True


# Отрицательный оффсет (-7, America/Hermosillo) → "-07:00" — деньги, знак критичен.
def test_negative_offset(monkeypatch) -> None:
    fake = _FakeClient(
        resp={"timezone_offset_hours_utc": -7, "timezone_name": "America/Hermosillo"}
    )
    client = _client_for(monkeypatch, fake)
    r = client.get("/api/campaigns/ad-account-timezone", params={"act_id": "123"})
    assert r.status_code == 200
    assert r.json()["tz_offset_str"] == "-07:00"
    assert r.json()["tz_offset_hours"] == -7


# Нулевой оффсет → "+00:00".
def test_zero_offset(monkeypatch) -> None:
    fake = _FakeClient(resp={"timezone_offset_hours_utc": 0, "timezone_name": "UTC"})
    client = _client_for(monkeypatch, fake)
    r = client.get("/api/campaigns/ad-account-timezone", params={"act_id": "act_999"})
    assert r.status_code == 200
    assert r.json()["tz_offset_str"] == "+00:00"


# Пустой act_id → 400 (даже до обращения к Meta).
def test_empty_act_id(monkeypatch) -> None:
    fake = _FakeClient(resp={"timezone_offset_hours_utc": 0})
    client = _client_for(monkeypatch, fake)
    r = client.get("/api/campaigns/ad-account-timezone", params={"act_id": "  act_  "})
    assert r.status_code == 400


# SessionUnavailableError (Vision-сессия не готова) → 503.
def test_session_unavailable_503(monkeypatch) -> None:
    fake = _FakeClient(raises=SessionUnavailableError("token_not_found"))
    client = _client_for(monkeypatch, fake)
    r = client.get("/api/campaigns/ad-account-timezone", params={"act_id": "act_1"})
    assert r.status_code == 503


# CircuitOpenError (circuit breaker OPEN) → 503.
def test_circuit_open_503(monkeypatch) -> None:
    fake = _FakeClient(raises=CircuitOpenError("meta-api", 60.0))
    client = _client_for(monkeypatch, fake)
    r = client.get("/api/campaigns/ad-account-timezone", params={"act_id": "act_1"})
    assert r.status_code == 503


# grpc.RpcError (browser-agent упал) → 503.
def test_grpc_error_503(monkeypatch) -> None:
    fake = _FakeClient(raises=grpc.RpcError("unavailable"))
    client = _client_for(monkeypatch, fake)
    r = client.get("/api/campaigns/ad-account-timezone", params={"act_id": "act_1"})
    assert r.status_code == 503


# Доменная ошибка Meta (NotFound — кабинет не существует) → 422.
def test_meta_not_found_422(monkeypatch) -> None:
    fake = _FakeClient(raises=NotFoundError("object does not exist", code=803))
    client = _client_for(monkeypatch, fake)
    r = client.get("/api/campaigns/ad-account-timezone", params={"act_id": "act_777"})
    assert r.status_code == 422


# Прочая доменная ошибка Meta (RateLimited) — кабинет дешёвый GET, не ретраим → 422.
# Важно: RateLimitedError — подкласс TemporaryError, но ловится РАНЬШЕ → остаётся 422.
def test_meta_rate_limited_422(monkeypatch) -> None:
    fake = _FakeClient(raises=RateLimitedError("throttled", code=4))
    client = _client_for(monkeypatch, fake)
    r = client.get("/api/campaigns/ad-account-timezone", params={"act_id": "act_1"})
    assert r.status_code == 422


# Транзиентный сбой канала Vision (TemporaryError, code -2 "Failed to fetch") → 503,
# НЕ 422: канал недоступен, кабинет не «битый» (инцидент 2026-06-19).
def test_temporary_error_503(monkeypatch) -> None:
    fake = _FakeClient(raises=TemporaryError("Failed to fetch", code=-2))
    client = _client_for(monkeypatch, fake)
    r = client.get("/api/campaigns/ad-account-timezone", params={"act_id": "act_1"})
    assert r.status_code == 503


# Кабинет есть, но Meta не вернула offset → 422 (как «не найден»).
def test_missing_offset_field_422(monkeypatch) -> None:
    fake = _FakeClient(resp={"timezone_name": "UTC"})  # без timezone_offset_hours_utc
    client = _client_for(monkeypatch, fake)
    r = client.get("/api/campaigns/ad-account-timezone", params={"act_id": "act_1"})
    assert r.status_code == 422


# act_id без обязательного query-параметра → 422 (валидация FastAPI).
def test_act_id_required(monkeypatch) -> None:
    fake = _FakeClient(resp={"timezone_offset_hours_utc": 0})
    client = _client_for(monkeypatch, fake)
    r = client.get("/api/campaigns/ad-account-timezone")
    assert r.status_code == 422


# _normalize_act_id снимает префикс act_ и пробелы.
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("act_123", "123"),
        ("123", "123"),
        ("  act_456  ", "456"),
        ("act_", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_act_id(raw, expected) -> None:
    assert _normalize_act_id(raw) == expected


# _tz_offset_to_str зеркалит алгоритм из campaigns_create (знак + zero-pad + :00).
@pytest.mark.parametrize(
    "hours,expected",
    [(-7, "-07:00"), (3, "+03:00"), (0, "+00:00"), (-12, "-12:00"), (10, "+10:00")],
)
def test_tz_offset_to_str(hours, expected) -> None:
    assert _tz_offset_to_str(hours) == expected

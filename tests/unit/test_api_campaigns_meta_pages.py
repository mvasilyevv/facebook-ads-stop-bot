# -*- coding: utf-8 -*-
"""Unit: GET /api/campaigns/ad-account-pages — список FB-страниц кабинета.

Проверяем парсинг promote_pages data → [{id,name}] (включая пустой список и
пропуск элементов без id) и маппинг ошибок: 503 (Vision/circuit/grpc/temporary),
422 (доменная ошибка Meta), 400 (пустой act_id).

Клиент Marketing API мокается целиком (execute_graph_call) — БЕЗ живой БД/Vision.
"""

from __future__ import annotations

from typing import Any

import grpc
from fastapi import FastAPI
from fastapi.testclient import TestClient

import apps.api.routers.v1.campaigns_meta as mod
from apps.api.deps import get_engine
from apps.api.routers.v1.campaigns_meta import router
from core.browser.circuit_breaker import CircuitOpenError
from core.meta_api.errors import (
    NotFoundError,
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


class _HarmlessBrowserOperationFence:
    """Unit-test substitute for the PostgreSQL-backed operation fence."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _HarmlessBrowserOperationFence:
        return self

    async def assert_held(self) -> None:
        return None

    async def __aexit__(self, *_args: Any) -> bool:
        return False


def _client_for(monkeypatch, fake: _FakeClient) -> TestClient:
    """FastAPI-приложение только с этим роутером; engine-dep и client замоканы."""
    monkeypatch.setattr(mod, "_build_meta_client", lambda engine: fake)
    monkeypatch.setattr(mod, "BrowserOperationFence", _HarmlessBrowserOperationFence)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_engine] = lambda: object()
    return TestClient(app, raise_server_exceptions=True)


# Две страницы парсятся в [{id,name}]; id/name приведены к строке.
def test_two_pages(monkeypatch) -> None:
    fake = _FakeClient(
        resp={
            "data": [
                {"id": 111, "name": "Brand A"},
                {"id": "222", "name": "Brand B"},
            ],
            "paging": {"cursors": {}},
        }
    )
    client = _client_for(monkeypatch, fake)
    r = client.get("/api/campaigns/ad-account-pages", params={"act_id": "act_123"})
    assert r.status_code == 200
    assert r.json() == {
        "pages": [
            {"id": "111", "name": "Brand A"},
            {"id": "222", "name": "Brand B"},
        ]
    }
    # Канал закрыли в finally; account identity передана явно.
    assert fake.closed is True
    assert fake.last_kwargs is not None
    assert fake.last_kwargs["ad_account_id"] == "act_123"
    assert fake.last_kwargs["endpoint"] == "/act_123/promote_pages"


# Пустой data → пустой список pages (валидный ответ, не ошибка).
def test_empty_data(monkeypatch) -> None:
    fake = _FakeClient(resp={"data": []})
    client = _client_for(monkeypatch, fake)
    r = client.get("/api/campaigns/ad-account-pages", params={"act_id": "123"})
    assert r.status_code == 200
    assert r.json() == {"pages": []}


# Отсутствие ключа data вовсе → пустой список (resp.get("data") or []).
def test_missing_data_key(monkeypatch) -> None:
    fake = _FakeClient(resp={})
    client = _client_for(monkeypatch, fake)
    r = client.get("/api/campaigns/ad-account-pages", params={"act_id": "123"})
    assert r.status_code == 200
    assert r.json() == {"pages": []}


# Элемент без id пропускается; name отсутствует → "".
def test_skip_item_without_id_and_empty_name(monkeypatch) -> None:
    fake = _FakeClient(
        resp={
            "data": [
                {"name": "No ID page"},  # пропускается
                {"id": "333"},  # name → ""
            ]
        }
    )
    client = _client_for(monkeypatch, fake)
    r = client.get("/api/campaigns/ad-account-pages", params={"act_id": "act_9"})
    assert r.status_code == 200
    assert r.json() == {"pages": [{"id": "333", "name": ""}]}


# Пустой/ненормализуемый act_id → единый Api validation status 422 до Meta.
def test_empty_act_id(monkeypatch) -> None:
    fake = _FakeClient(resp={"data": []})
    client = _client_for(monkeypatch, fake)
    r = client.get("/api/campaigns/ad-account-pages", params={"act_id": "  act_  "})
    assert r.status_code == 422


# act_id без обязательного query-параметра → 422 (валидация FastAPI).
def test_act_id_required(monkeypatch) -> None:
    fake = _FakeClient(resp={"data": []})
    client = _client_for(monkeypatch, fake)
    r = client.get("/api/campaigns/ad-account-pages")
    assert r.status_code == 422


# SessionUnavailableError (Vision-сессия не готова) → 503.
def test_session_unavailable_503(monkeypatch) -> None:
    fake = _FakeClient(raises=SessionUnavailableError("token_not_found"))
    client = _client_for(monkeypatch, fake)
    r = client.get("/api/campaigns/ad-account-pages", params={"act_id": "act_1"})
    assert r.status_code == 503


# CircuitOpenError (circuit breaker OPEN) → 503.
def test_circuit_open_503(monkeypatch) -> None:
    fake = _FakeClient(raises=CircuitOpenError("meta-api", 60.0))
    client = _client_for(monkeypatch, fake)
    r = client.get("/api/campaigns/ad-account-pages", params={"act_id": "act_1"})
    assert r.status_code == 503


# grpc.RpcError (browser-agent упал) → 503.
def test_grpc_error_503(monkeypatch) -> None:
    fake = _FakeClient(raises=grpc.RpcError("unavailable"))
    client = _client_for(monkeypatch, fake)
    r = client.get("/api/campaigns/ad-account-pages", params={"act_id": "act_1"})
    assert r.status_code == 503


# Транзиентный сбой канала Vision (TemporaryError) → 503, НЕ 422.
def test_temporary_error_503(monkeypatch) -> None:
    fake = _FakeClient(raises=TemporaryError("Failed to fetch", code=-2))
    client = _client_for(monkeypatch, fake)
    r = client.get("/api/campaigns/ad-account-pages", params={"act_id": "act_1"})
    assert r.status_code == 503


# Доменная ошибка Meta (NotFound — кабинет не существует) → 422.
def test_meta_not_found_422(monkeypatch) -> None:
    fake = _FakeClient(raises=NotFoundError("object does not exist", code=803))
    client = _client_for(monkeypatch, fake)
    r = client.get("/api/campaigns/ad-account-pages", params={"act_id": "act_777"})
    assert r.status_code == 422

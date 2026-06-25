# -*- coding: utf-8 -*-
"""Unit: offer-поля countries + default_page_id (схемы, нормализация, роутер).

Без живой БД: схемы тестируются напрямую, роутер — с фейковым async-engine,
который перехватывает values insert/update и отдаёт каноническую строку.
Покрываем: ISO-2 upper нормализацию, пустой countries → [], дефолты,
OfferOut со всеми 4 полями (pixel_id/ad_account_ids/countries/default_page_id),
персист в POST/PUT и возврат в GET.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.deps import get_engine
from apps.api.routers.v1.offers import router
from apps.api.routers.v1.schemas.offers import OfferCreateIn, OfferOut, OfferUpdateIn

# ─────────────────────── схемы: нормализация countries ───────────────────────


# countries приводятся к ISO-2 upper с дедупом и сохранением порядка.
def test_create_normalizes_countries_upper_dedup() -> None:
    body = OfferCreateIn(code="GH_CR2", ad_account_ids=["123"], countries=["de", "KE", "de"])
    assert body.countries == ["DE", "KE"]


# Пустой countries даёт [] (поле опционально, дефолт пустой список).
def test_create_empty_countries_defaults_to_empty_list() -> None:
    body = OfferCreateIn(code="GH_CR2", ad_account_ids=["123"])
    assert body.countries == []


# Невалидный код страны (не ISO-2) → ValueError при валидации.
def test_create_invalid_country_rejected() -> None:
    with pytest.raises(ValueError):
        OfferCreateIn(code="GH_CR2", ad_account_ids=["123"], countries=["DEU"])


# default_page_id опционален и по умолчанию None.
def test_create_default_page_id_optional() -> None:
    body = OfferCreateIn(code="GH_CR2", ad_account_ids=["123"])
    assert body.default_page_id is None


# В update countries=None — поле не трогаем (sentinel «не менять»).
def test_update_countries_none_means_untouched() -> None:
    body = OfferUpdateIn(countries=None)
    assert body.countries is None


# В update пустой countries=[] валиден (явная очистка гео).
def test_update_countries_empty_list_allowed() -> None:
    body = OfferUpdateIn(countries=[])
    assert body.countries == []


# В update countries нормализуются upper + дедуп.
def test_update_countries_normalized() -> None:
    body = OfferUpdateIn(countries=["br", "BR", "us"])
    assert body.countries == ["BR", "US"]


# ─────────────────────── OfferOut: все 4 поля ───────────────────────


# OfferOut.from_orm_offer отдаёт pixel_id + ad_account_ids + countries + default_page_id.
def test_offer_out_contains_all_four_fields() -> None:
    now = datetime.now(UTC)
    fake = SimpleNamespace(
        id=uuid.uuid4(),
        code="GH_CR2",
        name="GH_CR2",
        vertical="gambling",
        pixel_id="999000",
        is_active=True,
        ad_account_ids=["123", "456"],
        countries=["DE", "KE"],
        default_page_id="777111",
        created_at=now,
        updated_at=now,
    )
    out = OfferOut.from_orm_offer(fake)
    assert out.pixel_id == "999000"
    assert out.ad_account_ids == ["123", "456"]
    assert out.countries == ["DE", "KE"]
    assert out.default_page_id == "777111"


# OfferOut с пустым/отсутствующим countries даёт [] (стабильный shape).
def test_offer_out_missing_countries_defaults_empty() -> None:
    now = datetime.now(UTC)
    fake = SimpleNamespace(
        id=uuid.uuid4(),
        code="GH_CR2",
        name="GH_CR2",
        vertical=None,
        pixel_id=None,
        is_active=True,
        ad_account_ids=None,
        countries=None,
        default_page_id=None,
        created_at=now,
        updated_at=now,
    )
    out = OfferOut.from_orm_offer(fake)
    assert out.countries == []
    assert out.default_page_id is None


# ─────────────────────── роутер: фейковый async-engine ───────────────────────


class _FakeResult:
    """Результат execute: mappings().one()/one_or_none() отдаёт заданную строку."""

    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row
        self.rowcount = 1 if row is not None else 0

    def mappings(self) -> "_FakeResult":
        return self

    def one(self) -> dict[str, Any]:
        assert self._row is not None
        return self._row

    def one_or_none(self) -> dict[str, Any] | None:
        return self._row

    def all(self) -> list[dict[str, Any]]:
        return [self._row] if self._row is not None else []

    def first(self) -> Any:
        return self._row


class _FakeConn:
    """Перехватывает execute: запоминает values insert/update, возвращает каноническую строку."""

    def __init__(self, captured: dict[str, Any], row: dict[str, Any]) -> None:
        self._captured = captured
        self._row = row

    async def execute(self, stmt: Any, params: Any = None) -> _FakeResult:
        # Пытаемся вытащить values из INSERT/UPDATE-конструкции (compile params).
        compiled = getattr(stmt, "compile", None)
        if compiled is not None:
            try:
                self._captured.update(dict(stmt.compile().params))
            except Exception:  # noqa: BLE001 — best-effort, не все stmt компилируются
                pass
        return _FakeResult(self._row)


class _FakeBeginCtx:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _FakeEngine:
    def __init__(self, captured: dict[str, Any], row: dict[str, Any]) -> None:
        self._conn = _FakeConn(captured, row)

    def begin(self) -> _FakeBeginCtx:
        return _FakeBeginCtx(self._conn)

    def connect(self) -> _FakeBeginCtx:
        return _FakeBeginCtx(self._conn)


def _canonical_row(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    row = {
        "id": uuid.uuid4(),
        "code": "GH_CR2",
        "name": "GH_CR2",
        "vertical": "gambling",
        "pixel_id": "999000",
        "is_active": True,
        "ad_account_ids": ["123"],
        "countries": ["DE", "KE"],
        "default_page_id": "777111",
        "created_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    return row


def _client(captured: dict[str, Any], row: dict[str, Any]) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_engine] = lambda: _FakeEngine(captured, row)
    return TestClient(app, raise_server_exceptions=True)


# POST /offers персистит countries (upper) + default_page_id и отдаёт их в ответе.
def test_post_persists_and_returns_new_fields() -> None:
    captured: dict[str, Any] = {}
    client = _client(captured, _canonical_row())
    resp = client.post(
        "/api/offers",
        json={
            "code": "GH_CR2",
            "ad_account_ids": ["123"],
            "countries": ["de", "ke"],
            "default_page_id": "777111",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["countries"] == ["DE", "KE"]
    assert body["default_page_id"] == "777111"
    # В values insert попали нормализованные countries и страница.
    assert captured.get("countries") == ["DE", "KE"]
    assert captured.get("default_page_id") == "777111"


# PUT /offers/{id} заменяет countries (включая пустой) и default_page_id.
def test_put_updates_new_fields() -> None:
    captured: dict[str, Any] = {}
    oid = uuid.uuid4()
    client = _client(captured, _canonical_row(id=oid, countries=[], default_page_id=None))
    resp = client.put(
        f"/api/offers/{oid}",
        json={"countries": [], "default_page_id": ""},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["countries"] == []
    assert body["default_page_id"] is None
    # Пустой default_page_id → null в values update.
    assert captured.get("default_page_id") is None


# GET /offers отдаёт countries + default_page_id для каждого оффера.
def test_get_list_returns_new_fields() -> None:
    captured: dict[str, Any] = {}
    client = _client(captured, _canonical_row())
    resp = client.get("/api/offers")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert rows and rows[0]["countries"] == ["DE", "KE"]
    assert rows[0]["default_page_id"] == "777111"

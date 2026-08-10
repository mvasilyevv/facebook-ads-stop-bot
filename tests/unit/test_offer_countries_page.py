# -*- coding: utf-8 -*-
"""Unit: offer-поле countries (схемы, нормализация, роутер).

Без живой БД: схемы тестируются напрямую, роутер — с фейковым async-engine,
который перехватывает values insert/update и отдаёт каноническую строку.
Покрываем: ISO-2 upper нормализацию, пустой countries → [], дефолты,
OfferOut с pixel_id/ad_account_ids/countries (БЕЗ default_page_id —
страница задаётся per-campaign, откатано), персист в POST/PUT и возврат в GET.
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
    assert body.is_active is True


def test_create_accepts_explicit_inactive_state() -> None:
    body = OfferCreateIn(code="GH_CR2", ad_account_ids=["123"], is_active=False)
    assert body.is_active is False


# Невалидный код страны (не ISO-2) → ValueError при валидации.
def test_create_invalid_country_rejected() -> None:
    with pytest.raises(ValueError):
        OfferCreateIn(code="GH_CR2", ad_account_ids=["123"], countries=["DEU"])


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


@pytest.mark.parametrize(
    "retired_field",
    ("name", "country_code", "use_vision_creator", "notes"),
)
def test_create_rejects_retired_or_ignored_fields(retired_field: str) -> None:
    with pytest.raises(ValueError):
        OfferCreateIn.model_validate(
            {"code": "GH_CR2", "ad_account_ids": ["123"], retired_field: "legacy"}
        )


@pytest.mark.parametrize(
    "retired_field",
    ("code", "name", "country_code", "use_vision_creator", "notes"),
)
def test_update_rejects_immutable_or_retired_fields(retired_field: str) -> None:
    with pytest.raises(ValueError):
        OfferUpdateIn.model_validate({retired_field: "legacy"})


# ─────────────────────── OfferOut: pixel_id + ad_account_ids + countries ───────────────────────


# OfferOut.from_orm_offer отдаёт pixel_id + ad_account_ids + countries (без страницы).
def test_offer_out_contains_offer_fields() -> None:
    now = datetime.now(UTC)
    fake = SimpleNamespace(
        id=uuid.uuid4(),
        code="GH_CR2",
        name="GH_CR2",
        vertical="gambling",
        pixel_id="999000",
        is_active=True,
        countries=["DE", "KE"],
        created_at=now,
        updated_at=now,
    )
    out = OfferOut.from_orm_offer(fake, ad_account_ids=["456", "123"])
    assert out.pixel_id == "999000"
    assert out.ad_account_ids == ["123", "456"]
    assert out.countries == ["DE", "KE"]
    assert not hasattr(out, "default_page_id")
    assert not hasattr(out, "country_code")
    assert not hasattr(out, "use_vision_creator")
    assert not hasattr(out, "notes")


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
        countries=None,
        created_at=now,
        updated_at=now,
    )
    out = OfferOut.from_orm_offer(fake, ad_account_ids=[])
    assert out.countries == []


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


class _FakeMembershipResult:
    def __init__(self, rows: list[tuple[uuid.UUID, str]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[uuid.UUID, str]]:
        return self._rows


class _FakeConn:
    """Перехватывает execute: запоминает values insert/update, возвращает каноническую строку."""

    def __init__(self, captured: dict[str, Any], row: dict[str, Any]) -> None:
        self._captured = captured
        self._row = row

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        # Пытаемся вытащить values из INSERT/UPDATE-конструкции (compile params).
        compiled = getattr(stmt, "compile", None)
        if compiled is not None:
            try:
                self._captured.update(dict(stmt.compile().params))
            except Exception:  # noqa: BLE001 — best-effort, не все stmt компилируются
                pass
        if str(stmt).lstrip().startswith("SELECT") and "offer_ad_accounts" in str(stmt):
            return _FakeMembershipResult(
                [(self._row["id"], account_id) for account_id in self._row["ad_account_ids"]]
            )
        return _FakeResult(self._row)

    async def scalar(self, stmt: Any) -> uuid.UUID:
        return self._row["id"]


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
        "cpa_threshold": None,
        "currency": "USD",
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


# POST /offers персистит countries (upper) и отдаёт их в ответе.
def test_post_persists_and_returns_countries() -> None:
    captured: dict[str, Any] = {}
    client = _client(captured, _canonical_row(is_active=False))
    resp = client.post(
        "/api/offers",
        json={
            "code": "GH_CR2",
            "is_active": False,
            "ad_account_ids": ["123"],
            "countries": ["de", "ke"],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["countries"] == ["DE", "KE"]
    assert "default_page_id" not in body
    # В values insert попали нормализованные countries.
    assert captured.get("countries") == ["DE", "KE"]
    assert captured.get("is_active") is False
    assert "default_page_id" not in captured


# PUT /offers/{id} заменяет countries (включая пустой).
def test_put_updates_countries() -> None:
    captured: dict[str, Any] = {}
    oid = uuid.uuid4()
    client = _client(captured, _canonical_row(id=oid, countries=[]))
    resp = client.put(
        f"/api/offers/{oid}",
        json={"countries": []},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["countries"] == []
    assert "default_page_id" not in body
    # countries=[] долетает в values update.
    assert captured.get("countries") == []


# GET /offers отдаёт countries для каждого оффера (без default_page_id).
def test_get_list_returns_countries() -> None:
    captured: dict[str, Any] = {}
    client = _client(captured, _canonical_row())
    resp = client.get("/api/offers")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert rows and rows[0]["countries"] == ["DE", "KE"]
    assert "default_page_id" not in rows[0]

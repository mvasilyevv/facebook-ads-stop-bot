# -*- coding: utf-8 -*-
"""Unit: ApiKeyAuthMiddleware защищает write и чувствительные read-path.

write (POST/PUT/PATCH/DELETE) на не-исключённых путях → нужен корректный X-API-Key.
обычный read (GET) и исключённые пути (/api/v1/postback, /api/tma) — без ключа;
GET /api/ai/pulse требует ключ, потому что может инициировать платный AI.
require_api_key=False → enforcement выключен. api_key пуст + require → 503.

Middleware получает явный settings (SimpleNamespace) — тест не зависит от
глобального get_settings() и autouse-фикстуры conftest.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.middleware.api_key_auth import ApiKeyAuthMiddleware

_KEY = "secret-key-123"


def _app(
    *,
    require: bool = True,
    key: str = _KEY,
    tma_role: str | None = None,
) -> TestClient:
    app = FastAPI()

    async def authorize_tma(token: str, _settings) -> str | None:
        return tma_role if token == "valid-tma-token" else None

    app.add_middleware(
        ApiKeyAuthMiddleware,
        settings=SimpleNamespace(require_api_key=require, api_key=key),
        tma_authorizer=authorize_tma,
    )

    @app.get("/thing")
    async def _get():
        return {"ok": True}

    @app.post("/thing")
    async def _post():
        return {"ok": True}

    @app.get("/api/ai/pulse")
    async def _ai_pulse():
        return {"important": False}

    @app.post("/api/v1/postback/adsetpro")
    async def _postback():
        return {"ok": True}

    @app.post("/api/tma/draft-tasks/1/confirm")
    async def _tma():
        return {"ok": True}

    return TestClient(app)


# GET без ключа → 200 (read-only не требует ключа)
def test_get_without_key_allowed() -> None:
    assert _app().get("/thing").status_code == 200


# Чувствительный GET без ключа закрыт, с верным ключом доступен.
def test_ai_pulse_get_requires_key() -> None:
    client = _app()
    assert client.get("/api/ai/pulse").status_code == 401
    assert client.get("/api/ai/pulse", headers={"X-API-Key": _KEY}).status_code == 200


# POST без ключа → 401
def test_post_without_key_denied() -> None:
    assert _app().post("/thing").status_code == 401


# POST с неверным ключом → 401
def test_post_wrong_key_denied() -> None:
    r = _app().post("/thing", headers={"X-API-Key": "nope"})
    assert r.status_code == 401


# POST с верным ключом → 200
def test_post_correct_key_allowed() -> None:
    r = _app().post("/thing", headers={"X-API-Key": _KEY})
    assert r.status_code == 200


# Исключённый путь postback (свой секрет) → POST без ключа проходит
def test_postback_path_exempt() -> None:
    assert _app().post("/api/v1/postback/adsetpro").status_code == 200


# Исключённый путь TMA (Bearer) → POST без ключа проходит
def test_tma_path_exempt() -> None:
    assert _app().post("/api/tma/draft-tasks/1/confirm").status_code == 200


def test_tma_prefix_lookalike_is_not_exempt() -> None:
    assert _app().post("/api/tmanual-dangerous").status_code == 401


# require_api_key=False → enforcement выключен, POST без ключа проходит
def test_disabled_allows_write() -> None:
    assert _app(require=False).post("/thing").status_code == 200


# api_key пуст + require=True → 503 (явный отказ, не fail-open)
def test_empty_key_returns_503() -> None:
    assert _app(key="").post("/thing").status_code == 503


# OPTIONS (не write-метод) без ключа → не 401 (preflight не блокируем)
def test_options_not_blocked() -> None:
    r = _app().options("/thing")
    assert r.status_code != 401


def test_invalid_tma_bearer_cannot_bypass_auth_even_for_read() -> None:
    r = _app().get("/thing", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401


def test_active_tma_recipient_can_read_shared_api() -> None:
    r = _app(tma_role="recipient").get(
        "/thing", headers={"Authorization": "Bearer valid-tma-token"}
    )
    assert r.status_code == 200


def test_tma_recipient_cannot_use_shared_write_or_protected_read() -> None:
    client = _app(tma_role="recipient")
    headers = {"Authorization": "Bearer valid-tma-token"}

    assert client.post("/thing", headers=headers).status_code == 403
    assert client.get("/api/ai/pulse", headers=headers).status_code == 403


def test_tma_owner_can_use_shared_write_and_protected_read() -> None:
    client = _app(tma_role="owner")
    headers = {"Authorization": "Bearer valid-tma-token"}

    assert client.post("/thing", headers=headers).status_code == 200
    assert client.get("/api/ai/pulse", headers=headers).status_code == 200

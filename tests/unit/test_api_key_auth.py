# -*- coding: utf-8 -*-
"""Unit: ApiKeyAuthMiddleware защищает write и чувствительные read-path.

write (POST/PUT/PATCH/DELETE) на не-исключённых путях → нужен корректный X-API-Key.
обычный read (GET) и исключённые пути (/api/v1/postback, /api/tma) — без ключа;
POST /api/ai/pulse требует owner auth и exact Origin для panel cookie, потому
что cache miss может инициировать платный AI-вызов. GET отсутствует.
require_api_key=False → enforcement выключен. api_key пуст + require → 503.

Middleware получает явный settings (SimpleNamespace) — тест не зависит от
глобального get_settings() и autouse-фикстуры conftest.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.middleware.api_key_auth import ApiKeyAuthMiddleware, TmaAuthorization
from core.auth.panel_access import PANEL_SESSION_COOKIE

_KEY = "secret-key-123"


def _app(
    *,
    require: bool = True,
    key: str = _KEY,
    tma_role: str | None = None,
) -> TestClient:
    app = FastAPI()

    async def authorize_tma(token: str, _settings) -> TmaAuthorization | None:
        if token != "valid-tma-token" or tma_role is None:
            return None
        return TmaAuthorization(
            role=tma_role,
            telegram_user_id=424242,
            bot_generation=1,
        )

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

    @app.post("/principal")
    async def _principal(request: Request):
        return {"principal": getattr(request.state, "operator_principal", None)}

    @app.post("/api/thing")
    async def _api_post():
        return {"ok": True}

    @app.post("/api/ai/pulse")
    async def _ai_pulse():
        return {"important": False}

    @app.get("/api/tools/adset-duplicates/123")
    async def _adset_duplicate_status():
        return {"status": "draft"}

    @app.get("/api/settings/telegram")
    async def _telegram_settings():
        return {"activation_command": "/start owner-capability"}

    @app.get("/api/operator/preferences/display")
    async def _operator_display_preference(request: Request):
        return {
            "owner_id": getattr(
                request.state,
                "operator_owner_telegram_user_id",
                None,
            )
        }

    @app.post("/api/v1/postback/adsetpro")
    async def _postback():
        return {"ok": True}

    @app.post("/api/v1/internal/browser-operations/consume")
    async def _browser_consume():
        return {"ok": True}

    @app.post("/api/v1/internal/browser-operations/admin")
    async def _browser_admin():
        return {"ok": True}

    @app.post("/api/v1/internal/browser-maintenance/consume")
    async def _browser_maintenance_consume():
        return {"ok": True}

    @app.post("/api/v1/internal/browser-maintenance/admin")
    async def _browser_maintenance_admin():
        return {"ok": True}

    @app.post("/api/tma/draft-tasks/1/confirm")
    async def _tma():
        return {"ok": True}

    return TestClient(app)


# GET без ключа → 200 (read-only не требует ключа)
def test_get_without_key_allowed() -> None:
    assert _app().get("/thing").status_code == 200


# Платный side effect не доступен через GET; POST проходит общий write boundary.
def test_ai_pulse_is_post_only_and_requires_owner_auth() -> None:
    client = _app()
    assert client.get("/api/ai/pulse").status_code == 405
    assert client.post("/api/ai/pulse").status_code == 401
    assert client.post("/api/ai/pulse", headers={"X-API-Key": _KEY}).status_code == 200


def test_adset_duplicate_status_get_requires_owner_auth() -> None:
    client = _app()
    path = "/api/tools/adset-duplicates/123"

    assert client.get(path).status_code == 401
    assert client.get(path, headers={"X-API-Key": _KEY}).status_code == 200


def test_telegram_admin_reads_require_owner_auth() -> None:
    path = "/api/settings/telegram"
    assert _app().get(path).status_code == 401
    assert _app().get(path, headers={"X-API-Key": _KEY}).status_code == 200
    assert (
        _app(tma_role="recipient")
        .get(path, headers={"Authorization": "Bearer valid-tma-token"})
        .status_code
        == 403
    )


def test_operator_preferences_are_owner_only_and_bind_server_identity() -> None:
    path = "/api/operator/preferences/display"
    assert _app().get(path).status_code == 401
    assert (
        _app(tma_role="recipient")
        .get(path, headers={"Authorization": "Bearer valid-tma-token"})
        .status_code
        == 403
    )
    assert _app(tma_role="owner").get(
        path, headers={"Authorization": "Bearer valid-tma-token"}
    ).json() == {"owner_id": 424242}

    panel = _app().get(
        path,
        headers={
            "X-API-Key": _KEY,
            "X-Verified-Operator-Principal": "panel:424242",
        },
    )
    assert panel.json() == {"owner_id": 424242}

    # An API key alone is infrastructure authority, not an owner identity.
    assert _app().get(path, headers={"X-API-Key": _KEY}).json() == {"owner_id": None}
    assert (
        _app(tma_role="owner")
        .get(path, headers={"Authorization": "Bearer valid-tma-token"})
        .status_code
        == 200
    )


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


def test_only_exact_browser_consume_paths_are_exempt() -> None:
    client = _app()
    for path in (
        "/api/v1/internal/browser-operations/consume",
        "/api/v1/internal/browser-maintenance/consume",
    ):
        assert client.post(path).status_code == 200
    assert client.post("/api/v1/internal/browser-operations/admin").status_code == 401
    assert client.post("/api/v1/internal/browser-maintenance/admin").status_code == 401


# Исключённый путь TMA (Bearer) → POST без ключа проходит
def test_tma_path_exempt() -> None:
    assert _app().post("/api/tma/draft-tasks/1/confirm").status_code == 200


def test_tma_prefix_lookalike_is_not_exempt() -> None:
    assert _app().post("/api/tmanual-dangerous").status_code == 401


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
    assert client.post("/api/ai/pulse", headers=headers).status_code == 403
    assert client.get("/api/tools/adset-duplicates/123", headers=headers).status_code == 403
    assert client.get("/api/settings/telegram", headers=headers).status_code == 403


def test_tma_owner_can_use_shared_write_and_protected_read() -> None:
    client = _app(tma_role="owner")
    headers = {"Authorization": "Bearer valid-tma-token"}

    assert client.post("/thing", headers=headers).status_code == 200
    assert client.post("/api/ai/pulse", headers=headers).status_code == 200
    assert client.get("/api/tools/adset-duplicates/123", headers=headers).status_code == 200
    assert client.get("/api/settings/telegram", headers=headers).status_code == 200


def test_authenticated_boundary_owns_operator_principal() -> None:
    spoofed = {"X-Operator-Principal": "forged", "X-API-Key": _KEY}
    assert _app().post("/principal", headers=spoofed).json() == {"principal": "operator:web"}

    tma = _app(tma_role="owner").post(
        "/principal",
        headers={
            "Authorization": "Bearer valid-tma-token",
            "X-Operator-Principal": "forged",
        },
    )
    assert tma.json() == {"principal": "tma:424242"}


def test_verified_panel_identity_is_used_for_immutable_attribution() -> None:
    response = _app().post(
        "/principal",
        headers={
            "X-API-Key": _KEY,
            "X-Verified-Operator-Principal": "panel:424242",
        },
    )
    assert response.json() == {"principal": "operator:web:424242"}

    malformed = _app().post(
        "/principal",
        headers={
            "X-API-Key": _KEY,
            "X-Verified-Operator-Principal": "panel:owner",
        },
    )
    assert malformed.json() == {"principal": "operator:web"}


def test_cookie_authenticated_api_write_requires_exact_production_origin() -> None:
    client = _app()
    cookie = f"{PANEL_SESSION_COOKIE}=server-side-session"
    common = {"X-API-Key": _KEY, "Cookie": cookie}

    assert client.post("/api/thing", headers=common).status_code == 403
    assert (
        client.post(
            "/api/thing",
            headers={**common, "Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/thing",
            headers={**common, "Origin": "https://app.adpulse.su"},
        ).status_code
        == 200
    )


def test_tma_bearer_is_not_misclassified_as_cookie_auth() -> None:
    response = _app(tma_role="owner").post(
        "/api/thing",
        headers={
            "Authorization": "Bearer valid-tma-token",
            "Cookie": f"{PANEL_SESSION_COOKIE}=stale-panel-session",
        },
    )
    assert response.status_code == 200

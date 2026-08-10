# -*- coding: utf-8 -*-
"""Integration guards for the fail-fast versioned router registry."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.routers import v1 as registry


def _get_http_route_paths(app: FastAPI) -> set[str]:
    return set(app.openapi().get("paths", {}).keys())


def test_registry_names_every_v1_router_file() -> None:
    package_dir = Path(registry.__file__).parent
    modules = {path.stem for path in package_dir.glob("*.py") if path.stem != "__init__"}
    assert set(registry.ROUTER_MODULES) == modules


def test_create_app_registers_required_unprefixed_and_operator_routes() -> None:
    app = create_app()
    paths = _get_http_route_paths(app)
    assert {
        "/healthz",
        "/readyz",
        "/system-readyz",
        "/api/operator/snapshot",
        "/api/operator/actions",
        "/api/operator/ads",
        "/api/v1/integrations/telegram/webhook",
    } <= paths
    assert str(app.url_path_for("ws_operator")) == "/ws/operator"
    assert any("postback" in path for path in paths)


def test_register_all_fails_when_a_router_import_fails(monkeypatch) -> None:
    real_import = importlib.import_module

    def failing_import(name: str):
        if name == "apps.api.routers.v1.operator":
            raise ImportError("operator dependency missing")
        return real_import(name)

    monkeypatch.setattr(registry.importlib, "import_module", failing_import)

    with pytest.raises(ImportError, match="operator dependency missing"):
        registry.register_all(FastAPI())


def test_register_all_fails_when_module_has_no_router(monkeypatch) -> None:
    class MissingRouter:
        pass

    monkeypatch.setattr(registry, "ROUTER_MODULES", ("missing_router",))
    monkeypatch.setattr(
        registry.importlib,
        "import_module",
        lambda _: MissingRouter(),
    )

    with pytest.raises(RuntimeError, match="must expose router"):
        registry.register_all(FastAPI())


def test_app_starts_and_healthz_works() -> None:
    app = create_app()
    import fakeredis.aioredis as fakeredis_aio  # type: ignore[import-not-found]

    app.state.redis = fakeredis_aio.FakeRedis()
    client = TestClient(app, raise_server_exceptions=True)
    assert client.get("/healthz").status_code == 200

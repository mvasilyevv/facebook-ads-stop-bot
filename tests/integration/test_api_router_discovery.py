# -*- coding: utf-8 -*-
"""Интеграционный тест: auto-discovery роутеров из apps/api/routers/v1/.

Проверяет, что после `create_app()`:
- функция `register_all` отрабатывает без ошибок;
- все роутеры из apps/api/routers/v1/ подключены с префиксом /api;
- health/postback роутеры по-прежнему работают без префикса /api;
- добавление нового файла-заглушки в v1/ автоматически подхватывается.
"""

from __future__ import annotations

import sys
import types

from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.routers.v1 import register_all


# Структура URL-роутов app в виде set path-строк.
# В FastAPI >=0.135 include_router больше не разворачивает под-роуты в плоский
# app.routes — вместо этого появляется обёртка `_IncludedRouter` без атрибута .path,
# а реальные пути живут во внутреннем (приватном) дереве. Поэтому набор итоговых
# маршрутов берём из публичного контракта app.openapi()["paths"] — это стабильный
# источник финальных путей с учётом всех префиксов.
def _get_route_paths(app) -> set[str]:
    return set(app.openapi().get("paths", {}).keys())


# После create_app() приложение не падает и содержит оба уровня readiness.
def test_create_app_does_not_fail_with_empty_v1() -> None:
    # v1/ папка пустая (или без router-атрибутов) — create_app должен завершиться нормально.
    app = create_app()
    paths = _get_route_paths(app)
    assert "/healthz" in paths
    assert "/readyz" in paths
    assert "/system-readyz" in paths


# health и postback роутеры подключены без префикса /api.
def test_health_routes_have_no_api_prefix() -> None:
    app = create_app()
    paths = _get_route_paths(app)
    # /healthz без /api
    assert "/healthz" in paths
    # /api/v1/postback/adsetpro — postback сам уже имеет /api/v1 в своём пути
    assert any("postback" in p for p in paths)


# Заглушка-модуль с `router: APIRouter` подхватывается register_all.
def test_register_all_picks_up_module_with_router_attribute(tmp_path, monkeypatch) -> None:
    from fastapi import APIRouter, FastAPI

    # Создаём временный модуль в памяти с атрибутом `router`.
    fake_module_name = "apps.api.routers.v1._test_stub_module"
    fake_module = types.ModuleType(fake_module_name)
    stub_router = APIRouter()

    @stub_router.get("/stub-route")
    async def _stub():
        return {"stub": True}

    fake_module.router = stub_router
    monkeypatch.setitem(sys.modules, fake_module_name, fake_module)

    # Патчим pkgutil.iter_modules так, чтобы вернул наш stub-модуль.
    import pkgutil

    original_iter = pkgutil.iter_modules

    def _fake_iter(path=None, prefix=""):
        yield pkgutil.ModuleInfo(None, "_test_stub_module", False)

    monkeypatch.setattr(pkgutil, "iter_modules", _fake_iter)

    app = FastAPI()
    register_all(app)

    # Роутер зарегистрирован с префиксом /api.
    paths = _get_route_paths(app)
    assert "/api/stub-route" in paths

    # Восстанавливаем iter_modules.
    monkeypatch.setattr(pkgutil, "iter_modules", original_iter)


# Модуль без атрибута `router` не вызывает ошибку и не регистрируется.
def test_register_all_skips_module_without_router(monkeypatch) -> None:
    from fastapi import FastAPI

    fake_module_name = "apps.api.routers.v1._test_no_router_module"
    fake_module = types.ModuleType(fake_module_name)
    # Нет атрибута router.
    monkeypatch.setitem(sys.modules, fake_module_name, fake_module)

    import pkgutil

    original_iter = pkgutil.iter_modules

    def _fake_iter(path=None, prefix=""):
        yield pkgutil.ModuleInfo(None, "_test_no_router_module", False)

    monkeypatch.setattr(pkgutil, "iter_modules", _fake_iter)

    app = FastAPI()
    # Не должно бросать исключение.
    register_all(app)

    # Никакой /api/... маршрут не добавлен.
    paths = _get_route_paths(app)
    assert not any(p.startswith("/api") for p in paths)

    monkeypatch.setattr(pkgutil, "iter_modules", original_iter)


# Тест, что реальный TestClient с create_app() стартует без ошибок.
def test_app_starts_and_healthz_works() -> None:
    app = create_app()
    # Подставляем state.redis, чтобы lifespan не пытался подключиться к Redis.
    import fakeredis.aioredis as fakeredis_aio  # type: ignore[import-not-found]

    app.state.redis = fakeredis_aio.FakeRedis()
    client = TestClient(app, raise_server_exceptions=True)
    resp = client.get("/healthz")
    # /healthz всегда 200, не зависит от Postgres/Redis.
    assert resp.status_code == 200

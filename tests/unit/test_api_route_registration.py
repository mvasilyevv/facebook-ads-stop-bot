# -*- coding: utf-8 -*-
"""Тесты синхронизации маршрутов фронтенда и FastAPI."""

from __future__ import annotations

import re
from pathlib import Path


def _normalize_route_path(path: str) -> str:
    """Нормализует динамические сегменты роутов до общего шаблона."""
    return re.sub(r"\{[^}]+\}", "{param}", path)


def _normalize_front_path(path: str) -> str:
    """Нормализует шаблонные литералы фронтенда до общего шаблона.

    Отбрасывает query-string — бэкенд-роуты сравниваются без неё.
    """
    without_query = path.split("?", 1)[0]
    return re.sub(r"\$\{[^}]+\}", "{param}", without_query)


def _collect_backend_routes() -> set[tuple[str, str]]:
    """Собирает зарегистрированные FastAPI-роуты без служебных HEAD/OPTIONS."""
    from apps.api.main import app

    routes: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set())
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            routes.add((method, _normalize_route_path(path)))
    return routes


def _collect_frontend_api_routes() -> set[tuple[str, str]]:
    """Парсит frontend API client и собирает ожидаемые пути/методы."""
    source = Path("frontend/src/api.js").read_text()

    routes: set[tuple[str, str]] = set()

    static_with_method = re.finditer(
        r"""request\(\s*['"](?P<path>/[^'"`]+)['"]\s*,\s*\{[^}]*method:\s*['"](?P<method>[A-Z]+)['"]""",
        source,
        re.S,
    )
    for match in static_with_method:
        routes.add((match.group("method"), f"/api{match.group('path')}"))

    template_with_method = re.finditer(
        r"""request\(\s*`(?P<path>/[^`]+)`\s*,\s*\{[^}]*method:\s*['"](?P<method>[A-Z]+)['"]""",
        source,
        re.S,
    )
    for match in template_with_method:
        routes.add((match.group("method"), f"/api{_normalize_front_path(match.group('path'))}"))

    static_get = re.finditer(
        r"""request\(\s*['"](?P<path>/[^'"`]+)['"]\s*\)""",
        source,
    )
    for match in static_get:
        routes.add(("GET", f"/api{match.group('path')}"))

    template_get = re.finditer(
        r"""request\(\s*`(?P<path>/[^`]+)`\s*\)""",
        source,
    )
    for match in template_get:
        routes.add(("GET", f"/api{_normalize_front_path(match.group('path'))}"))

    request_with_query = re.finditer(
        r"""requestWithQuery\(\s*['"](?P<path>/[^'"`]+)['"]""",
        source,
    )
    for match in request_with_query:
        routes.add(("GET", f"/api{match.group('path')}"))

    return routes


# Проверяем, что каждый маршрут из frontend api.js реально зарегистрирован в FastAPI с тем же методом.
def test_frontend_api_routes_are_registered_in_fastapi():
    frontend_routes = _collect_frontend_api_routes()
    backend_routes = _collect_backend_routes()

    missing = sorted(route for route in frontend_routes if route not in backend_routes)
    assert missing == []


# Проверяем, что новый маршрут фронтенда проверки колонок и старый серверный маршрут доступны одновременно.
def test_browser_validate_columns_routes_keep_frontend_and_legacy_paths():
    backend_routes = _collect_backend_routes()

    assert ("GET", "/api/settings/browser/validate-columns") in backend_routes
    assert ("GET", "/api/browser/validate-columns") in backend_routes


# Проверяем, что после подключения роутеров не появляются пути с двойным префиксом /api/api.
def test_registered_routes_do_not_have_double_api_prefix():
    backend_routes = _collect_backend_routes()

    invalid = sorted(path for _, path in backend_routes if path.startswith("/api/api/"))
    assert invalid == []

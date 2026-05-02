# -*- coding: utf-8 -*-
"""Тесты границ фронтенд API-слоя."""

from __future__ import annotations

from pathlib import Path

FRONTEND_SRC = Path("frontend/src")
ALLOWED_FETCH_FILES = {
    FRONTEND_SRC / "api.js",
    FRONTEND_SRC / "api.test.js",
    # Shared API-клиент — тоже официальный HTTP-слой
    FRONTEND_SRC / "shared" / "api.js",
}


def _frontend_source_files() -> list[Path]:
    """Возвращает все исходники фронтенда, которые стоит проверять на сетевые вызовы."""
    return sorted(
        path for path in FRONTEND_SRC.rglob("*") if path.suffix in {".js", ".jsx", ".ts", ".tsx"}
    )


# Проверяем, что только общий API-клиент использует прямой fetch/axios/XHR.
def test_frontend_network_calls_are_centralized_in_api_client():
    disallowed_hits: list[str] = []

    for path in _frontend_source_files():
        if path in ALLOWED_FETCH_FILES:
            continue
        text = path.read_text()
        if "fetch(" in text or "axios" in text or "XMLHttpRequest" in text:
            disallowed_hits.append(str(path))

    assert disallowed_hits == []


# Проверяем, что сырой префикс /api не разъезжается по страницам и хукам мимо api.js.
def test_frontend_api_prefix_is_not_used_outside_api_client():
    disallowed_hits: list[str] = []

    for path in _frontend_source_files():
        if path in ALLOWED_FETCH_FILES:
            continue
        text = path.read_text()
        if "/api/" in text or '"/api' in text or "'/api" in text:
            disallowed_hits.append(str(path))

    assert disallowed_hits == []

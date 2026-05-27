# -*- coding: utf-8 -*-
"""Unit: валидация CORS в apps.api.main.create_app() factory.

Покрывает HIGH #12 из security audit: wildcard "*" с allow_credentials=True
— мгновенный CSRF, должен падать на старте, а не тихо открывать origin.
"""

from __future__ import annotations

import pytest
from fastapi.middleware.cors import CORSMiddleware

from apps.api.main import create_app
from core.config import Settings


def _patch_settings(monkeypatch, **overrides) -> None:
    """Подменяет get_settings в apps.api.main на инстанс с заданными полями."""
    settings = Settings(**overrides)
    monkeypatch.setattr("apps.api.main.get_settings", lambda: settings)


def _has_cors_middleware(app) -> bool:
    """True, если CORSMiddleware подключён к app."""
    return any(m.cls is CORSMiddleware for m in app.user_middleware)


# Wildcard "*" + credentials=True = CSRF: create_app() обязан упасть на старте.
def test_create_app_raises_on_cors_wildcard(monkeypatch) -> None:
    _patch_settings(monkeypatch, frontend_origin="*")
    with pytest.raises(RuntimeError, match="CORS wildcard"):
        create_app()


# Wildcard внутри списка origins (если кто-то впишет "https://app.com,*") — тоже падаем.
def test_create_app_raises_on_cors_wildcard_in_csv(monkeypatch) -> None:
    _patch_settings(monkeypatch, frontend_origin="https://app.com,*")
    with pytest.raises(RuntimeError, match="CORS wildcard"):
        create_app()


# frontend_origin=None → CORS-middleware не подключаем вообще (дефолт прод-конфига без фронта).
def test_create_app_skips_cors_when_origin_not_set(monkeypatch) -> None:
    _patch_settings(monkeypatch, frontend_origin=None)
    app = create_app()
    assert not _has_cors_middleware(app)


# Явный http origin → CORSMiddleware подключается на этот один origin.
def test_create_app_attaches_cors_for_explicit_origin(monkeypatch) -> None:
    origin = "http://localhost:5173"
    _patch_settings(monkeypatch, frontend_origin=origin)
    app = create_app()
    # Найдём конфиг CORSMiddleware в стэке middleware и проверим origins/credentials.
    cors_layer = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
    assert cors_layer.kwargs["allow_origins"] == [origin]
    assert cors_layer.kwargs["allow_credentials"] is True

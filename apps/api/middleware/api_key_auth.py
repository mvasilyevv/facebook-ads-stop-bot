# -*- coding: utf-8 -*-
"""Middleware: X-API-Key на write-эндпоинтах (H-3).

API биндится на 0.0.0.0 + Ingress (доступен извне), а write-операции —
money-критичны (выключить авто-стоп, рестартнуть observer, подтвердить
money-черновик). Поэтому POST/PUT/PATCH/DELETE требуют заголовок `X-API-Key`,
совпадающий с `settings.api_key` (timing-safe сравнение).

Что НЕ проверяется (свой механизм / публичность):
- GET/HEAD/OPTIONS — read-only, ключ не требуется (минимум риска для дашбордов).
- `/healthz`, `/readyz`, `/metrics` — k8s/Prometheus (это и так GET).
- `/api/v1/postback/*` — внешний постбэк трекера со своим `X-Postback-Secret`.
- `/api/tma/*` — Telegram Mini App с Bearer-токеном (TMA initData).

Флаг `settings.require_api_key` (secure-by-default True) позволяет выключить
enforcement (тесты — autouse-фикстура; локальная разработка без ключа).
"""

from __future__ import annotations

import logging
import secrets

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.config import Settings, get_settings, reveal_secret

logger = logging.getLogger(__name__)

# Методы, меняющие состояние, — требуют ключ. Read-only (GET/HEAD/OPTIONS) — нет.
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Префиксы путей со своим механизмом auth или публичные — ключ не требуем.
_EXEMPT_PATH_PREFIXES = ("/api/v1/postback", "/api/tma")


class ApiKeyAuthMiddleware(BaseHTTPMiddleware):
    """Требует X-API-Key на write-методах (кроме исключённых путей).

    settings — для тестируемости можно подать явный объект; по умолчанию берётся
    ленивый синглтон get_settings() в момент запроса (чтобы tests/conftest мог
    выставить require_api_key=False на лету).
    """

    def __init__(self, app, *, settings: Settings | None = None) -> None:
        super().__init__(app)
        self._settings = settings

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        settings = self._settings or get_settings()

        if not settings.require_api_key:
            return await call_next(request)
        if request.method.upper() not in _WRITE_METHODS:
            return await call_next(request)
        if any(request.url.path.startswith(p) for p in _EXEMPT_PATH_PREFIXES):
            return await call_next(request)

        expected = reveal_secret(settings.api_key) if settings.api_key else ""
        if not expected:
            # Ключ не сконфигурирован, но enforcement включён → явный отказ
            # (не fail-open: иначе тихо открыли бы money-эндпоинты).
            logger.error("require_api_key=True, но api_key пуст — write-эндпоинты закрыты (503)")
            return JSONResponse(
                status_code=503,
                content={"detail": "API_KEY не сконфигурирован на сервере"},
            )

        provided = request.headers.get("x-api-key") or ""
        if not provided or not secrets.compare_digest(provided, expected):
            return JSONResponse(
                status_code=401,
                content={"detail": "Требуется корректный X-API-Key для write-операций"},
            )

        return await call_next(request)

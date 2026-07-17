# -*- coding: utf-8 -*-
"""Middleware: server-side API key or validated TMA Bearer on protected paths.

API биндится на 0.0.0.0 + Ingress (доступен извне), а write-операции —
money-критичны (выключить авто-стоп, рестартнуть observer, подтвердить
money-черновик). Desktop POST/PUT/PATCH/DELETE требуют `X-API-Key`, который в
production добавляет Caddy только после BasicAuth. Mini App может использовать
общий API с подписанным Bearer: любой токен проверяется и для read, write требует
актуальную роль owner.

Что НЕ проверяется (свой механизм / публичность):
- обычные GET/HEAD/OPTIONS — read-only, ключ не требуется (минимум риска для дашбордов);
- исключение: GET `/api/ai/pulse` защищён, потому что раскрывает операционные
  факты и может инициировать платный AI-вызов;
- `/healthz`, `/readyz`, `/metrics` — k8s/Prometheus (это и так GET).
- `/api/v1/postback/*` — внешний постбэк трекера со своим `X-Postback-Secret`.
- `/api/tma/*` — Telegram Mini App с Bearer-токеном (TMA initData).

Флаг `settings.require_api_key` (secure-by-default True) позволяет выключить
enforcement (тесты — autouse-фикстура; локальная разработка без ключа).
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.auth.tma import InvalidInitDataError, verify_session_token
from core.config import Settings, get_settings, reveal_secret
from core.db import get_engine
from core.telegram.service import find_recipient_by_telegram_user_id

logger = logging.getLogger(__name__)

# Методы, меняющие состояние, — требуют ключ. Read-only (GET/HEAD/OPTIONS) — нет.
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Точечные read-endpoint'ы с чувствительными данными/платным side effect.
_PROTECTED_READ_PATHS = frozenset({"/api/ai/pulse"})
_PROTECTED_READ_PREFIXES = ("/api/tools/adset-duplicates/",)

# Префиксы путей со своим механизмом auth или публичные — ключ не требуем.
_EXEMPT_PATH_PREFIXES = ("/api/v1/postback", "/api/tma", "/desktop/logout")

TmaAuthorizer = Callable[[str, Settings], Awaitable[str | None]]


def _has_own_auth(path: str) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in _EXEMPT_PATH_PREFIXES)


async def _authorize_tma_bearer(token: str, settings: Settings) -> str | None:
    """Return the current recipient role for a valid TMA session token.

    General Mini App requests use the shared desktop API surface.  Production
    Caddy routes Bearer requests here without its BasicAuth/API-key injection,
    so this check is the fail-closed trust boundary for both reads and writes.
    Recipient state is re-read on every request so revocation remains immediate.
    """
    if settings.tma_session_secret:
        secret = reveal_secret(settings.tma_session_secret)
    else:
        secret = reveal_secret(settings.encryption_key)
    if not secret or not token:
        return None
    try:
        payload = verify_session_token(token, secret, settings.tma_session_ttl_seconds)
        telegram_user_id = int(payload.get("telegram_user_id", 0) or 0)
    except (InvalidInitDataError, TypeError, ValueError):
        return None
    if telegram_user_id <= 0:
        return None
    recipient = await find_recipient_by_telegram_user_id(
        get_engine(), telegram_user_id=telegram_user_id
    )
    return recipient.role if recipient is not None else None


class ApiKeyAuthMiddleware(BaseHTTPMiddleware):
    """Требует X-API-Key на write-методах и защищённых read-path.

    settings — для тестируемости можно подать явный объект; по умолчанию берётся
    ленивый синглтон get_settings() в момент запроса (чтобы tests/conftest мог
    выставить require_api_key=False на лету).
    """

    def __init__(
        self,
        app,
        *,
        settings: Settings | None = None,
        tma_authorizer: TmaAuthorizer | None = None,
    ) -> None:
        super().__init__(app)
        self._settings = settings
        self._tma_authorizer = tma_authorizer or _authorize_tma_bearer

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        settings = self._settings or get_settings()

        if not settings.require_api_key:
            return await call_next(request)
        method = request.method.upper()
        is_protected_read = method in {"GET", "HEAD"} and (
            request.url.path in _PROTECTED_READ_PATHS
            or any(request.url.path.startswith(prefix) for prefix in _PROTECTED_READ_PREFIXES)
        )
        if _has_own_auth(request.url.path):
            return await call_next(request)

        authorization = request.headers.get("authorization") or ""
        if authorization.startswith("Bearer "):
            token = authorization[len("Bearer ") :].strip()
            role = await self._tma_authorizer(token, settings)
            if role is None:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "TMA Bearer-токен невалиден, истёк или доступ отозван"},
                )
            if (method in _WRITE_METHODS or is_protected_read) and role != "owner":
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Эта операция TMA доступна только владельцу"},
                )
            return await call_next(request)

        if method not in _WRITE_METHODS and not is_protected_read:
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
                content={"detail": "Требуется корректный X-API-Key для защищённой операции"},
            )

        return await call_next(request)

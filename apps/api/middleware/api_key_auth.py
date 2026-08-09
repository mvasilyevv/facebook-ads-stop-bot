# -*- coding: utf-8 -*-
"""Middleware: server-side API key or validated TMA Bearer on protected paths.

API биндится на 0.0.0.0 за Caddy (доступен извне), а write-операции —
money-критичны (выключить авто-стоп, рестартнуть observer, подтвердить
money-черновик). Desktop POST/PUT/PATCH/DELETE требуют `X-API-Key`, который в
production добавляет Caddy только после cookie forward_auth. Mini App может использовать
общий API с подписанным Bearer: любой токен проверяется и для read, write требует
актуальную роль owner.

Что НЕ проверяется (свой механизм / публичность):
- обычные GET/HEAD/OPTIONS — read-only, ключ не требуется (минимум риска для дашбордов);
- `/healthz`, `/readyz`, `/metrics` — platform probes/Prometheus (это и так GET).
- `/api/v1/postback/*` — внешний GET postback трекера со своим query token.
- `/api/tma/*` — Telegram Mini App с Bearer-токеном (TMA initData).

Флаг `settings.require_api_key` (secure-by-default True) позволяет выключить
enforcement (тесты — autouse-фикстура; локальная разработка без ключа).
"""

from __future__ import annotations

import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from apps.api.middleware.api_problem import api_problem_payload, request_correlation_id
from core.auth.panel_access import PANEL_SESSION_COOKIE
from core.auth.tma import InvalidInitDataError, verify_session_token
from core.config import Settings, get_settings, reveal_secret
from core.db import get_engine
from core.telegram.service import (
    find_recipient_by_telegram_user_id,
    telegram_generation_is_authoritative,
)

logger = logging.getLogger(__name__)

# Методы, меняющие состояние, — требуют ключ. Read-only (GET/HEAD/OPTIONS) — нет.
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Read-endpoint'ы с административными или чувствительными данными.
_PROTECTED_READ_PREFIXES = (
    "/api/operator/preferences/",
    "/api/tools/adset-duplicates/",
    "/api/tools/campaigns/draft",
    # Telegram settings expose recipient identities, delivery diagnostics and
    # owner-invite capabilities.  They are administration data, not a shared
    # TMA read surface.
    "/api/settings/telegram",
)

# Префиксы путей со своим механизмом auth или публичные — ключ не требуем.
_EXEMPT_PATHS = frozenset(
    {
        "/api/v1/internal/browser-maintenance/consume",
        "/api/v1/internal/browser-operations/consume",
    }
)
_EXEMPT_PATH_PREFIXES = (
    "/api/v1/postback",
    "/api/v1/integrations/telegram/webhook",
    "/api/v1/integrations/alertmanager/webhook",
    "/api/tma",
    "/desktop/logout",
)


@dataclass(frozen=True)
class TmaAuthorization:
    role: str
    telegram_user_id: int
    bot_generation: int


TmaAuthorizer = Callable[[str, Settings], Awaitable[TmaAuthorization | None]]
_PRODUCTION_PANEL_ORIGIN = "https://app.adpulse.su"
_VERIFIED_PANEL_PRINCIPAL = re.compile(r"^panel:([1-9][0-9]*)$")


def _auth_error(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    correlation_id = request_correlation_id(request.scope)
    return JSONResponse(
        status_code=status_code,
        content=api_problem_payload(
            code=code,
            message=message,
            correlation_id=correlation_id,
        ),
        headers={"X-Request-Id": correlation_id},
    )


def _has_own_auth(path: str) -> bool:
    return path in _EXEMPT_PATHS or any(
        path == prefix or path.startswith(f"{prefix}/") for prefix in _EXEMPT_PATH_PREFIXES
    )


async def _authorize_tma_bearer(token: str, settings: Settings) -> TmaAuthorization | None:
    """Return immutable recipient identity for a valid TMA session token.

    General Mini App requests use the shared desktop API surface.  Production
    Caddy routes Bearer requests here without its BasicAuth/API-key injection,
    so this check is the fail-closed trust boundary for both reads and writes.
    Recipient state is re-read on every request so revocation remains immediate.
    """
    secret = reveal_secret(settings.tma_session_secret) if settings.tma_session_secret else ""
    if not secret or not token:
        return None
    try:
        payload = verify_session_token(token, secret, settings.tma_session_ttl_seconds)
        telegram_user_id = int(payload.get("telegram_user_id", 0) or 0)
        bot_generation = int(payload.get("bot_generation", 0) or 0)
    except (InvalidInitDataError, TypeError, ValueError):
        return None
    if telegram_user_id <= 0 or bot_generation <= 0:
        return None
    engine = get_engine()
    if not await telegram_generation_is_authoritative(
        engine,
        bot_generation=bot_generation,
    ):
        return None
    recipient = await find_recipient_by_telegram_user_id(engine, telegram_user_id=telegram_user_id)
    if recipient is None:
        return None
    return TmaAuthorization(
        role=recipient.role,
        telegram_user_id=telegram_user_id,
        bot_generation=bot_generation,
    )


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
        method = request.method.upper()
        authorization = request.headers.get("authorization") or ""

        # A valid panel cookie is ambient browser authority. Require the exact
        # production Origin on every cookie-authenticated state change so an
        # external site cannot drive the API through the operator's session.
        if (
            method in _WRITE_METHODS
            and request.url.path.startswith("/api/")
            and PANEL_SESSION_COOKIE in request.cookies
            and not authorization.startswith("Bearer ")
            and request.headers.get("origin") != _PRODUCTION_PANEL_ORIGIN
        ):
            return _auth_error(
                request,
                status_code=403,
                code="invalid_origin",
                message="Недопустимый Origin для panel write-запроса",
            )

        if not settings.require_api_key:
            return await call_next(request)
        is_protected_read = method in {"GET", "HEAD"} and (
            any(request.url.path.startswith(prefix) for prefix in _PROTECTED_READ_PREFIXES)
        )
        if _has_own_auth(request.url.path):
            return await call_next(request)

        if authorization.startswith("Bearer "):
            token = authorization[len("Bearer ") :].strip()
            authorization_context = await self._tma_authorizer(token, settings)
            if authorization_context is None:
                return _auth_error(
                    request,
                    status_code=401,
                    code="invalid_tma_session",
                    message="TMA Bearer-токен невалиден, истёк или доступ отозван",
                )
            if (
                method in _WRITE_METHODS or is_protected_read
            ) and authorization_context.role != "owner":
                return _auth_error(
                    request,
                    status_code=403,
                    code="owner_role_required",
                    message="Эта операция TMA доступна только владельцу",
                )
            request.state.operator_principal = f"tma:{authorization_context.telegram_user_id}"
            if authorization_context.role == "owner":
                request.state.operator_owner_telegram_user_id = (
                    authorization_context.telegram_user_id
                )
            return await call_next(request)

        if method not in _WRITE_METHODS and not is_protected_read:
            return await call_next(request)

        expected = reveal_secret(settings.api_key) if settings.api_key else ""
        if not expected:
            # Ключ не сконфигурирован, но enforcement включён → явный отказ
            # (не fail-open: иначе тихо открыли бы money-эндпоинты).
            logger.error("require_api_key=True, но api_key пуст — write-эндпоинты закрыты (503)")
            return _auth_error(
                request,
                status_code=503,
                code="api_key_unconfigured",
                message="API_KEY не сконфигурирован на сервере",
            )

        provided = request.headers.get("x-api-key") or ""
        if not provided or not secrets.compare_digest(provided, expected):
            return _auth_error(
                request,
                status_code=401,
                code="invalid_api_key",
                message="Требуется корректный X-API-Key для защищённой операции",
            )

        # Audit identity is derived only at the trusted authentication boundary.
        # A browser-supplied X-Operator-Principal is ignored.
        verified_panel = request.headers.get("x-verified-operator-principal") or ""
        panel_match = _VERIFIED_PANEL_PRINCIPAL.fullmatch(verified_panel)
        if panel_match is not None:
            owner_telegram_user_id = int(panel_match.group(1))
            request.state.operator_principal = f"operator:web:{owner_telegram_user_id}"
            request.state.operator_owner_telegram_user_id = owner_telegram_user_id
        else:
            request.state.operator_principal = "operator:web"
        return await call_next(request)

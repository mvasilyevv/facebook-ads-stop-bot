# -*- coding: utf-8 -*-
"""FastAPI control plane for the FB Agent operator platform.

Что внутри:
- lifespan: создаёт общие Redis/MetaApi клиенты и закрывает их на shutdown.
  Engine не создаём — берётся через `core.db.get_engine` (ленивый синглтон).
- Middleware: canonical ApiProblem, X-Request-Id и Prometheus-метрики.
- CORS: подключается только если задан `settings.frontend_origin`.
- Exception handlers: validation, HTTP, AdSet.pro, Meta and unexpected errors
  share one non-secret four-field response contract.
- Routers: operator, integrations, settings, health and WebSocket surfaces.

Использование:
    uvicorn apps.api.main:app --host 0.0.0.0 --port 8100

Тесты создают app через `create_app()`, чтобы каждый тест-модуль получил свежий
экземпляр (и мог подменить `app.state.redis` под fakeredis).
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from redis.asyncio import from_url as redis_from_url  # type: ignore[import-not-found]
from starlette.exceptions import HTTPException as StarletteHTTPException

from apps.api.metrics import REQUEST_DURATION, REQUESTS_TOTAL
from apps.api.middleware.api_key_auth import ApiKeyAuthMiddleware
from apps.api.middleware.api_problem import (
    NON_API_PROBE_PATHS,
    ApiProblemMiddleware,
    api_problem_payload,
    default_problem_code,
    default_problem_message,
    request_correlation_id,
)
from apps.api.middleware.body_size import BodySizeLimitMiddleware
from apps.api.middleware.request_id import RequestIdMiddleware
from apps.api.routers import desktop_auth as desktop_auth_router
from apps.api.routers import health as health_router
from apps.api.routers import panel_auth as panel_auth_router
from apps.api.routers import postback as postback_router
from apps.api.routers import ws as ws_router
from apps.api.routers.v1 import register_all as register_v1_routers
from apps.api.schemas.problem import ApiProblem
from core.adset_pro import (
    AdsetProError,
)
from core.adset_pro import (
    AuthError as AdsetProAuthError,
)
from core.adset_pro import (
    NotFoundError as AdsetProNotFoundError,
)
from core.adset_pro import (
    RateLimitedError as AdsetProRateLimitedError,
)
from core.adset_pro import (
    TemporaryError as AdsetProTemporaryError,
)
from core.config import get_settings, safe_url_for_log
from core.crypto import validate_encryption_material
from core.meta_api.client import MetaApiClient
from core.meta_api.errors import (
    MetaApiError,
)
from core.meta_api.errors import (
    NotFoundError as MetaNotFoundError,
)
from core.meta_api.errors import (
    PermissionError as MetaPermissionError,
)
from core.meta_api.errors import (
    RateLimitedError as MetaRateLimitedError,
)
from core.meta_api.errors import (
    SessionUnavailableError as MetaSessionUnavailableError,
)
from core.meta_api.errors import (
    TokenInvalidError as MetaTokenInvalidError,
)
from core.telemetry import instrument_fastapi

logger = logging.getLogger(__name__)


async def _build_meta_api_client() -> MetaApiClient | None:
    """Создаёт общий MetaApiClient для READ-инструментов веб-чата.

    gRPC-канал сам восстанавливает соединение с browser-agent, поэтому держим
    один клиент на весь lifespan API вместо нового канала на каждый вопрос.
    """
    grpc_host = (
        os.environ.get("BROWSER_AGENT_GRPC_HOST")
        or os.environ.get("BROWSER_AGENT_HOST")
        or "localhost"
    )
    try:
        grpc_port = int(os.environ.get("BROWSER_AGENT_GRPC_PORT", "50051"))
    except ValueError:
        grpc_port = 50051

    try:
        client = MetaApiClient(host=grpc_host, port=grpc_port)
        await client.start()
        logger.info(
            "MetaApiClient создан в API lifespan (%s:%d) — Meta READ tools веб-чата активны",
            grpc_host,
            grpc_port,
        )
        return client
    except Exception as exc:  # noqa: BLE001 — API должен подняться даже без browser-agent
        logger.warning("MetaApiClient не создан в API lifespan: %s", exc)
        return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Поднимает Redis и MetaApiClient на старте, закрывает на остановке.

    Engine не трогаем — `core.db.get_engine` это ленивый синглгтон, dispose
    делать на каждый рестарт уvicorn worker'а не обязательно (gunicorn/uvicorn
    обычно сами kill'ят процесс целиком). При желании можно расширить.

    Если `app.state.redis` уже задан (тесты переопределяют fakeredis'ом) —
    не пересоздаём.
    """
    settings = get_settings()
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").strip().lower() == "production":
        validate_encryption_material()
    own_redis = False
    own_meta_api_client = False
    if not getattr(app.state, "redis", None):
        app.state.redis = redis_from_url(settings.redis_url, decode_responses=True)
        own_redis = True
        logger.info("Redis-клиент создан в lifespan: %s", safe_url_for_log(settings.redis_url))
    if not getattr(app.state, "meta_api_client", None):
        app.state.meta_api_client = await _build_meta_api_client()
        own_meta_api_client = app.state.meta_api_client is not None
    app.state.settings = settings
    try:
        yield
    finally:
        if own_meta_api_client and getattr(app.state, "meta_api_client", None) is not None:
            try:
                await app.state.meta_api_client.close()
            except Exception as exc:
                logger.warning("Ошибка закрытия MetaApiClient в lifespan: %s", exc)
        if own_redis and getattr(app.state, "redis", None) is not None:
            try:
                await app.state.redis.aclose()
            except Exception as exc:
                logger.warning("Ошибка закрытия Redis в lifespan: %s", exc)


def create_app() -> FastAPI:
    """Factory FastAPI-приложения. Тесты создают свой экземпляр через эту функцию."""
    settings = get_settings()
    app = FastAPI(
        title="FB Agent Operator API",
        description="Safety-first control, observability and integration API.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # BodySizeLimit — ПЕРВЫМ add_middleware = ВНУТРЕННИЙ слой (ближайший к app).
    # Критично для chunked-пути (H-9, ревью #3): limited_receive должен кормить FastAPI
    # НАПРЯМУЮ — тогда 413 (fastapi.HTTPException) из receive ре-рейзится штатным
    # `except HTTPException` парсера body. Если между BodySize и app стоит
    # BaseHTTPMiddleware (RequestId), исключение из receive рвётся об его внутренний
    # стрим — FastAPI видит ClientDisconnect → 400 «error parsing the body».
    app.add_middleware(BodySizeLimitMiddleware)

    # CORS — только если фронт сконфигурирован (в проде/dev).
    # Wildcard "*" + allow_credentials=True = мгновенный CSRF (см. security audit HIGH #12).
    # Падаем на старте: лучше отказ деплоя, чем тихо открытый origin.
    if settings.frontend_origin:
        if "*" in settings.frontend_origin:
            raise RuntimeError(
                "CORS wildcard with credentials=True is forbidden "
                "(frontend_origin must be explicit, например http://localhost:5173)"
            )
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[settings.frontend_origin],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.add_middleware(RequestIdMiddleware)
    # H-3: X-API-Key на write-эндпоинтах (secure-by-default; см. ApiKeyAuthMiddleware).
    app.add_middleware(ApiKeyAuthMiddleware)

    # Метрики — middleware ставится через декоратор, поэтому ниже add_middleware.
    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start
        # route.path даёт template (например, /api/v1/postback/adsetpro),
        # а не raw URL — иначе кардинальность лейбла взорвалась бы.
        route = request.scope.get("route")
        path = getattr(route, "path", None) or request.url.path
        REQUESTS_TOTAL.labels(
            path=path, method=request.method, status=str(response.status_code)
        ).inc()
        REQUEST_DURATION.labels(path=path, method=request.method).observe(duration)
        return response

    # Exception handlers — маппят доменные ошибки на HTTP.
    _register_exception_handlers(app)

    # Routers.
    # health и postback — без префикса /api (используются probes/Prometheus и внешними сервисами).
    # ws — DB-authoritative WebSocket без префикса /api (`/ws/operator`).
    app.include_router(panel_auth_router.router)
    app.include_router(desktop_auth_router.router)
    app.include_router(health_router.router)
    app.include_router(postback_router.router)
    app.include_router(ws_router.router)

    # Полный reviewed registry v1 подключается fail-fast с префиксом /api.
    # Новый router обязан быть явно добавлен в registry и contract tests.
    register_v1_routers(app)
    instrument_fastapi(app)
    # Last added = outermost user middleware. It also normalizes failures
    # returned directly by CORS/auth/body-size middleware, not only exceptions.
    app.add_middleware(ApiProblemMiddleware)
    _install_api_problem_openapi(app)

    return app


def _register_exception_handlers(app: FastAPI) -> None:
    """Register one safe ``ApiProblem`` contract for all HTTP failures."""

    def _response(
        request: Request,
        *,
        status_code: int,
        code: str,
        message: str,
        field_errors: dict[str, list[str]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> JSONResponse:
        correlation_id = request_correlation_id(request.scope)
        response_headers = dict(headers or {})
        response_headers["X-Request-Id"] = correlation_id
        return JSONResponse(
            status_code=status_code,
            content=api_problem_payload(
                code=code,
                message=message,
                correlation_id=correlation_id,
                field_errors=field_errors,
            ),
            headers=response_headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        field_errors: dict[str, list[str]] = {}
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
            field_errors.setdefault(location or "request", []).append(
                str(error.get("msg", "Invalid value"))[:512]
            )
        return _response(
            request,
            status_code=422,
            code="validation_error",
            message="Параметры запроса не прошли проверку",
            field_errors=field_errors,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # HTTPException.detail is an explicitly public client message for 4xx.
        # For 5xx, never mirror details that may contain an upstream exception,
        # endpoint URL or credential fragment.
        public_detail = exc.detail if isinstance(exc.detail, str) else None
        message = (
            public_detail[:1024]
            if public_detail and exc.status_code < 500
            else default_problem_message(exc.status_code)
        )
        return _response(
            request,
            status_code=exc.status_code,
            code=default_problem_code(exc.status_code),
            message=message,
            headers=exc.headers,
        )

    @app.exception_handler(AdsetProAuthError)
    async def _adsetpro_auth(request: Request, _exc: AdsetProAuthError) -> JSONResponse:
        return _response(
            request,
            status_code=401,
            code="adsetpro_auth",
            message="AdSet.pro authorization failed",
        )

    @app.exception_handler(AdsetProNotFoundError)
    async def _adsetpro_not_found(request: Request, _exc: AdsetProNotFoundError) -> JSONResponse:
        return _response(
            request,
            status_code=404,
            code="adsetpro_not_found",
            message="AdSet.pro resource not found",
        )

    @app.exception_handler(AdsetProRateLimitedError)
    async def _adsetpro_rl(request: Request, _exc: AdsetProRateLimitedError) -> JSONResponse:
        return _response(
            request,
            status_code=429,
            code="adsetpro_rate_limited",
            message="AdSet.pro rate limit exceeded",
        )

    @app.exception_handler(AdsetProTemporaryError)
    async def _adsetpro_temp(request: Request, _exc: AdsetProTemporaryError) -> JSONResponse:
        return _response(
            request,
            status_code=503,
            code="adsetpro_temporary",
            message="AdSet.pro is temporarily unavailable",
        )

    @app.exception_handler(AdsetProError)
    async def _adsetpro_base(request: Request, _exc: AdsetProError) -> JSONResponse:
        # Базовый AdsetProError (Permanent без узкого подтипа) → 502.
        return _response(
            request,
            status_code=502,
            code="adsetpro",
            message="AdSet.pro request failed",
        )

    @app.exception_handler(MetaTokenInvalidError)
    async def _meta_token_invalid(request: Request, _exc: MetaTokenInvalidError) -> JSONResponse:
        return _response(
            request,
            status_code=401,
            code="meta_token_invalid",
            message="Meta authentication is unavailable",
        )

    @app.exception_handler(MetaPermissionError)
    async def _meta_permission(request: Request, _exc: MetaPermissionError) -> JSONResponse:
        return _response(
            request,
            status_code=403,
            code="meta_permission",
            message="Meta denied the requested operation",
        )

    @app.exception_handler(MetaNotFoundError)
    async def _meta_not_found(request: Request, _exc: MetaNotFoundError) -> JSONResponse:
        return _response(
            request,
            status_code=404,
            code="meta_not_found",
            message="Meta resource not found",
        )

    @app.exception_handler(MetaRateLimitedError)
    async def _meta_rl(request: Request, _exc: MetaRateLimitedError) -> JSONResponse:
        return _response(
            request,
            status_code=429,
            code="meta_rate_limited",
            message="Meta rate limit exceeded",
        )

    @app.exception_handler(MetaSessionUnavailableError)
    async def _meta_session(request: Request, _exc: MetaSessionUnavailableError) -> JSONResponse:
        return _response(
            request,
            status_code=503,
            code="meta_session_unavailable",
            message="Meta browser session is temporarily unavailable",
        )

    @app.exception_handler(MetaApiError)
    async def _meta_base(request: Request, _exc: MetaApiError) -> JSONResponse:
        return _response(
            request,
            status_code=502,
            code="meta_api",
            message="Meta request failed",
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        correlation_id = request_correlation_id(request.scope)
        # Never interpolate ``exc``: exception strings frequently contain raw
        # upstream responses, signed URLs or credentials.
        logger.error(
            "Unhandled HTTP exception type=%s correlation_id=%s",
            type(exc).__name__,
            correlation_id,
        )
        return _response(
            request,
            status_code=500,
            code="internal_error",
            message=default_problem_message(500),
        )


def _install_api_problem_openapi(app: FastAPI) -> None:
    """Declare the runtime-wide error envelope for every HTTP operation."""

    def _openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        components["ApiProblem"] = ApiProblem.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
        problem_content = {
            "application/json": {"schema": {"$ref": "#/components/schemas/ApiProblem"}}
        }
        for path, path_item in schema.get("paths", {}).items():
            if path in NON_API_PROBE_PATHS:
                continue
            for method, operation in path_item.items():
                if method not in {"get", "put", "post", "delete", "options", "head", "patch"}:
                    continue
                responses = operation.setdefault("responses", {})
                for status_code, response in responses.items():
                    if str(status_code).startswith(("4", "5")):
                        response["content"] = problem_content
                responses["default"] = {
                    "description": "Canonical API error",
                    "content": problem_content,
                }
        # FastAPI creates these default validation components before the
        # operation responses are rewritten. No operation references them
        # after the canonical ApiProblem pass, so keeping them would expose a
        # second, impossible client error contract to code generators.
        components.pop("HTTPValidationError", None)
        components.pop("ValidationError", None)
        app.openapi_schema = schema
        return schema

    app.openapi = _openapi  # type: ignore[method-assign]


app = create_app()

# -*- coding: utf-8 -*-
"""FastAPI app для FB Stop Bot (минимальная версия после миграции).

Что внутри:
- lifespan: создаёт общие Redis/MetaApi клиенты и закрывает их на shutdown.
  Engine не создаём — берётся через `core.db.get_engine` (ленивый синглтон).
- Middleware: X-Request-Id + сбор Prometheus-метрик.
- CORS: подключается только если задан `settings.frontend_origin`.
- Exception handlers: `AdsetProError`/`MetaApiError` → корректные HTTP-статусы
  без 500-stacktrace для клиента.
- Routers: health (/healthz, /readyz, /metrics) + postback (/api/v1/postback/adsetpro).

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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import from_url as redis_from_url  # type: ignore[import-not-found]

from apps.api.metrics import REQUEST_DURATION, REQUESTS_TOTAL
from apps.api.middleware.api_key_auth import ApiKeyAuthMiddleware
from apps.api.middleware.body_size import BodySizeLimitMiddleware
from apps.api.middleware.request_id import RequestIdMiddleware
from apps.api.routers import desktop_auth as desktop_auth_router
from apps.api.routers import health as health_router
from apps.api.routers import panel_auth as panel_auth_router
from apps.api.routers import postback as postback_router
from apps.api.routers import ws as ws_router
from apps.api.routers.v1 import register_all as register_v1_routers
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
        title="FB Stop Bot API",
        description="Минимальный набор endpoints после миграции.",
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
    # health и postback — без префикса /api (используются k8s/Prometheus и внешними сервисами).
    # ws — WebSocket без префикса /api (фронт коннектится на /ws/dashboard).
    app.include_router(panel_auth_router.router)
    app.include_router(desktop_auth_router.router)
    app.include_router(health_router.router)
    app.include_router(postback_router.router)
    app.include_router(ws_router.router)

    # Все роутеры из apps/api/routers/v1/ подключаются автоматически с префиксом /api.
    # Spawn B/C/D просто кладут файл с атрибутом `router: APIRouter` в эту папку.
    register_v1_routers(app)

    return app


def _register_exception_handlers(app: FastAPI) -> None:
    """Регистрирует handlers для AdsetProError и MetaApiError."""

    @app.exception_handler(AdsetProAuthError)
    async def _adsetpro_auth(_request: Request, exc: AdsetProAuthError) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": str(exc), "kind": "adsetpro_auth"})

    @app.exception_handler(AdsetProNotFoundError)
    async def _adsetpro_not_found(_request: Request, exc: AdsetProNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404, content={"detail": str(exc), "kind": "adsetpro_not_found"}
        )

    @app.exception_handler(AdsetProRateLimitedError)
    async def _adsetpro_rl(_request: Request, exc: AdsetProRateLimitedError) -> JSONResponse:
        return JSONResponse(
            status_code=429, content={"detail": str(exc), "kind": "adsetpro_rate_limited"}
        )

    @app.exception_handler(AdsetProTemporaryError)
    async def _adsetpro_temp(_request: Request, exc: AdsetProTemporaryError) -> JSONResponse:
        return JSONResponse(
            status_code=503, content={"detail": str(exc), "kind": "adsetpro_temporary"}
        )

    @app.exception_handler(AdsetProError)
    async def _adsetpro_base(_request: Request, exc: AdsetProError) -> JSONResponse:
        # Базовый AdsetProError (Permanent без узкого подтипа) → 502.
        return JSONResponse(status_code=502, content={"detail": str(exc), "kind": "adsetpro"})

    @app.exception_handler(MetaTokenInvalidError)
    async def _meta_token_invalid(_request: Request, exc: MetaTokenInvalidError) -> JSONResponse:
        return JSONResponse(
            status_code=401, content={"detail": str(exc), "kind": "meta_token_invalid"}
        )

    @app.exception_handler(MetaPermissionError)
    async def _meta_permission(_request: Request, exc: MetaPermissionError) -> JSONResponse:
        return JSONResponse(
            status_code=403, content={"detail": str(exc), "kind": "meta_permission"}
        )

    @app.exception_handler(MetaNotFoundError)
    async def _meta_not_found(_request: Request, exc: MetaNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc), "kind": "meta_not_found"})

    @app.exception_handler(MetaRateLimitedError)
    async def _meta_rl(_request: Request, exc: MetaRateLimitedError) -> JSONResponse:
        return JSONResponse(
            status_code=429, content={"detail": str(exc), "kind": "meta_rate_limited"}
        )

    @app.exception_handler(MetaSessionUnavailableError)
    async def _meta_session(_request: Request, exc: MetaSessionUnavailableError) -> JSONResponse:
        return JSONResponse(
            status_code=503, content={"detail": str(exc), "kind": "meta_session_unavailable"}
        )

    @app.exception_handler(MetaApiError)
    async def _meta_base(_request: Request, exc: MetaApiError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc), "kind": "meta_api"})


app = create_app()

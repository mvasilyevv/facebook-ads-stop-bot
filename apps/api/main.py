# -*- coding: utf-8 -*-
"""FastAPI app для FB Stop Bot (минимальная версия после v2-миграции).

Что внутри:
- lifespan: создаёт Redis-клиент и кладёт в app.state.redis (закрывает на shutdown).
  Engine не создаём — берётся через `core.db.get_engine` (ленивый синглтон).
- Middleware: X-Request-Id + сбор Prometheus-метрик.
- CORS: подключается только если задан `settings.frontend_origin`.
- Exception handlers: `AdsetProError`/`MetaApiError` → корректные HTTP-статусы
  без 500-stacktrace для клиента.
- Routers: health (/healthz, /readyz, /metrics) + postback (/api/v1/postback/adsetpro).

Использование:
    uvicorn apps.api.main:app --host 0.0.0.0 --port 8000

Тесты создают app через `create_app()`, чтобы каждый тест-модуль получил свежий
экземпляр (и мог подменить `app.state.redis` под fakeredis).
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import from_url as redis_from_url  # type: ignore[import-not-found]

from apps.api.metrics import REQUEST_DURATION, REQUESTS_TOTAL
from apps.api.middleware.request_id import RequestIdMiddleware
from apps.api.routers import health as health_router
from apps.api.routers import postback as postback_router
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
from core.config import get_settings
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Поднимает Redis на старте, закрывает на остановке.

    Engine не трогаем — `core.db.get_engine` это ленивый синглгтон, dispose
    делать на каждый рестарт уvicorn worker'а не обязательно (gunicorn/uvicorn
    обычно сами kill'ят процесс целиком). При желании можно расширить.

    Если `app.state.redis` уже задан (тесты переопределяют fakeredis'ом) —
    не пересоздаём.
    """
    settings = get_settings()
    own_redis = False
    if not getattr(app.state, "redis", None):
        app.state.redis = redis_from_url(settings.redis_url, decode_responses=True)
        own_redis = True
        logger.info("Redis-клиент создан в lifespan: %s", settings.redis_url)
    app.state.settings = settings
    try:
        yield
    finally:
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
        description="Минимальный набор endpoints после v2-миграции.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS — только если фронт сконфигурирован (в проде/dev).
    if settings.frontend_origin:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[settings.frontend_origin],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.add_middleware(RequestIdMiddleware)

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
    app.include_router(health_router.router)
    app.include_router(postback_router.router)

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

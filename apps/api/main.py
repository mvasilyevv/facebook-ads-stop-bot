from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from apps.api.bootstrap import bootstrap_reference_data
from apps.api.config import load_settings
from apps.api.routers import (
    ads,
    control_flags,
    decisions,
    health,
    offers,
    profile_launches,
    profiles,
    rules,
    scan_runs,
    sessions,
)
from apps.api.routers import (
    settings as settings_router,
)


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


api_settings = load_settings()
_setup_logging(api_settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await bootstrap_reference_data(api_settings)
    logger.info("API успешно инициализировано и подключено к базе данных")
    yield


app = FastAPI(
    title=api_settings.app_name,
    debug=api_settings.debug,
    docs_url="/docs" if api_settings.docs_enabled else None,
    redoc_url="/redoc" if api_settings.docs_enabled else None,
    openapi_url="/openapi.json" if api_settings.docs_enabled else None,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(api_settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    logger.warning("Ошибка проверки входных данных. Количество ошибок: %s", len(exc.errors()))
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Ошибка проверки входных данных",
            "errors_count": len(exc.errors()),
        },
    )


@app.exception_handler(RuntimeError)
async def runtime_exception_handler(request: Request, exc: RuntimeError) -> JSONResponse:
    logger.error("Системная ошибка: %s", exc)
    return JSONResponse(status_code=500, content={"detail": str(exc)})


app.include_router(health.router)
app.include_router(profiles.router)
app.include_router(profile_launches.router)
app.include_router(ads.router)
app.include_router(decisions.router)
app.include_router(scan_runs.router)
app.include_router(rules.router)
app.include_router(offers.router)
app.include_router(control_flags.router)
app.include_router(sessions.router)
app.include_router(settings_router.router)

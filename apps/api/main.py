from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from apps.api.config import load_settings
from apps.api.routers import (
    ads,
    control_flags,
    decisions,
    health,
    offers,
    rules,
    scan_runs,
    sessions,
)
from apps.api.services.state import build_api_state


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


settings = load_settings()
_setup_logging(settings.log_level)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
)


@app.on_event("startup")
async def on_startup() -> None:
    app.state.api_state = build_api_state(settings)
    logger.info("API-каркас успешно инициализирован")


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
app.include_router(ads.router)
app.include_router(decisions.router)
app.include_router(scan_runs.router)
app.include_router(rules.router)
app.include_router(offers.router)
app.include_router(control_flags.router)
app.include_router(sessions.router)

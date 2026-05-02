# -*- coding: utf-8 -*-
"""FastAPI приложение — полный API для UI dashboard с подключением к БД.

Включает:
- Настройки (observer, Telegram)
- Управление офферами и правилами
- Dashboard: отключённые объявления, статистика, история алертов
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_db, require_api_key_or_tma, verify_api_key
from apps.api.routers import (
    campaign_scripts,
    creative_tools,
    dashboard,
    fake_deposits,
    health,
    history,
    naming_tracker,
    offers,
    settings,
    tma,
    vision_telegram,
)
from core.config import get_settings
from core.db import get_engine
from core.db.base import Base
from core.sentry import setup_sentry

# Инициализируем Sentry как можно раньше, до создания приложения
_s = get_settings()
setup_sentry(dsn=_s.sentry_dsn, environment=_s.sentry_environment)

_startup_logger = logging.getLogger(__name__)


# ==========================================
# Lifespan — инициализация БД
# ==========================================


def _has_alembic_migrations() -> bool:
    """Проверяет, есть ли в проекте реальные Alembic-миграции."""
    versions_dir = Path(__file__).resolve().parents[2] / "migrations" / "versions"
    if not versions_dir.exists():
        return False
    return any(
        path.suffix == ".py" and path.name != "__init__.py" for path in versions_dir.iterdir()
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Создаём таблицы только когда проект работает без Alembic-миграций."""
    engine = get_engine()
    if not _has_alembic_migrations():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # Предупреждение при старте, если API_KEY не задан
    _cfg = get_settings()
    if not _cfg.api_key:
        _startup_logger.warning(
            "ВНИМАНИЕ: API_KEY не задан — API будет доступен только с localhost. "
            "Установите API_KEY в .env перед деплоем в production."
        )

    yield
    await engine.dispose()


app = FastAPI(title="FB Stop Bot API", version="0.1.0", lifespan=lifespan)

# Общая зависимость API-ключа для всех роутеров (кроме /health)
_api_key_dep = [Depends(verify_api_key)]
# Зависимость для роутеров, доступных и из UI, и из mini-app
_api_key_or_tma_dep = [Depends(require_api_key_or_tma)]

# CORS для React-фронтенда (localhost-порты Vite)
_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:5175",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
    "http://127.0.0.1:5175",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Включаем роутеры для основного API (с аутентификацией по API-ключу)
app.include_router(offers.router, dependencies=_api_key_or_tma_dep)
app.include_router(settings.router, dependencies=_api_key_or_tma_dep)
app.include_router(dashboard.router, dependencies=_api_key_or_tma_dep)
app.include_router(vision_telegram.router, dependencies=_api_key_dep)
app.include_router(history.router, dependencies=_api_key_dep)
app.include_router(fake_deposits.router, dependencies=_api_key_dep)
app.include_router(naming_tracker.router, dependencies=_api_key_dep)
app.include_router(creative_tools.router, dependencies=_api_key_dep)
app.include_router(campaign_scripts.router, dependencies=_api_key_dep)
# Health-check роутер без авторизации
app.include_router(health.router)
# TMA роутер (аутентификация собственная, через initData)
app.include_router(tma.router)


# ==========================================
# Health check endpoint
# ==========================================


class HealthResponse(BaseModel):
    status: str = "ok"
    db: str = "ok"


@app.get("/health", response_model=HealthResponse)
async def health(db: AsyncSession = Depends(get_db)):
    """Проверяет доступность API и базы данных."""
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"
    return HealthResponse(status="ok", db=db_status)

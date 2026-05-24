# -*- coding: utf-8 -*-
"""FastAPI приложение — полный API для UI dashboard с подключением к БД.

Включает:
- Настройки (observer, Telegram)
- Управление офферами и правилами
- Dashboard: отключённые объявления, статистика, история алертов
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.logging import setup_logging
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app as _make_prometheus_asgi_app
from pydantic import BaseModel
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_db, require_api_key_or_tma, verify_api_key
from apps.api.routers import (
    ai,
    campaign_scripts,
    creative_tools,
    dashboard,
    fake_deposits,
    health,
    history,
    naming_tracker,
    observer,
    offers,
    settings,
    tma,
    vision_telegram,
)
from apps.api.routers.campaign_creator import router as campaign_creator_router
from apps.api.routers.campaign_recorder import router as campaign_recorder_router
from apps.api.routers.ws import router as ws_router
from core.config import get_settings
from core.db import get_engine, get_session_factory
from core.db.base import Base
from core.models import ScanRun
from core.observer.scan_run_writer import mark_interrupted_runs
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


_housekeeping_logger = logging.getLogger(__name__)


async def _scan_runs_housekeeping_loop() -> None:
    """Каждые 5 мин помечает зависшие RUNNING-черновики как INTERRUPTED.
    Раз в сутки удаляет scan_runs старше 30 дней.
    """
    factory = get_session_factory()
    next_retention_at = datetime.now(UTC)
    while True:
        try:
            async with factory() as session:
                cutoff = datetime.now(UTC) - timedelta(minutes=5)
                marked = await mark_interrupted_runs(session, older_than=cutoff)
                await session.commit()
                if marked:
                    _housekeeping_logger.info(
                        "scan_runs: %d черновиков помечены INTERRUPTED", marked
                    )

                if datetime.now(UTC) >= next_retention_at:
                    retention_cutoff = datetime.now(UTC) - timedelta(days=30)
                    result = await session.execute(
                        delete(ScanRun).where(ScanRun.finished_at < retention_cutoff)
                    )
                    await session.commit()
                    if result.rowcount:
                        _housekeeping_logger.info(
                            "scan_runs: %d старых строк удалено", result.rowcount
                        )
                    next_retention_at = datetime.now(UTC) + timedelta(days=1)
        except Exception:
            _housekeeping_logger.exception("scan_runs housekeeping упал, продолжаю через 5 мин")
        await asyncio.sleep(5 * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Создаём таблицы только когда проект работает без Alembic-миграций."""
    setup_logging("api")
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

    # Фоновая задача housekeeping scan_runs
    housekeeping_task = asyncio.create_task(
        _scan_runs_housekeeping_loop(),
        name="scan-runs-housekeeping",
    )

    yield

    housekeeping_task.cancel()
    try:
        await housekeeping_task
    except asyncio.CancelledError:
        pass
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
app.include_router(observer.router, dependencies=_api_key_or_tma_dep)
app.include_router(vision_telegram.router, dependencies=_api_key_dep)
app.include_router(history.router, dependencies=_api_key_dep)
app.include_router(fake_deposits.router, dependencies=_api_key_dep)
app.include_router(naming_tracker.router, dependencies=_api_key_dep)
app.include_router(creative_tools.router, dependencies=_api_key_dep)
app.include_router(campaign_scripts.router, dependencies=_api_key_dep)
# AI-анализ — доступен из web-UI и mini-app
app.include_router(ai.router, dependencies=_api_key_or_tma_dep)
# Health-check роутер без авторизации
app.include_router(health.router)
# TMA роутер (аутентификация собственная, через initData)
app.include_router(tma.router)
# Campaign Recorder — без авторизации (внутренний инструмент)
app.include_router(campaign_recorder_router, dependencies=_api_key_dep)
# Campaign Creator — запуск автосоздания кампаний
app.include_router(campaign_creator_router, dependencies=_api_key_dep)
# WebSocket — realtime-события для дашборда (без явной аутентификации для MVP)
app.include_router(ws_router)

# Prometheus-метрики — открыт без аутентификации (защищается на уровне reverse-proxy/VPN)
_metrics_app = _make_prometheus_asgi_app()
app.mount("/metrics", _metrics_app)


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

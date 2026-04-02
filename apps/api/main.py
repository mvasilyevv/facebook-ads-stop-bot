# -*- coding: utf-8 -*-
"""FastAPI приложение — полный API для UI dashboard с подключением к БД.

Включает:
- Настройки (observer, Telegram)
- Управление офферами и правилами
- Dashboard: отключённые объявления, статистика, история алертов
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_db
from apps.api.routers import dashboard, offers, settings, vision_telegram
from apps.api.routes.miniapp import router as miniapp_router
from apps.api.schemas import (  # noqa: F401 - re-exported for backward compatibility
    ActiveIncidentSchema,
    AdDiagnosticsSchema,
    AdSnapshotSchema,
    AlertEventSchema,
    ChartDataSchema,
    CreateDisableTaskRequest,
    CurrentEnableRecommendationRow,
    DashboardBatchSchema,
    DashboardPerformanceSchema,
    DashboardStatsSchema,
    DisableTaskSchema,
    EnableRecommendationEventSchema,
    EnableTaskSchema,
    InviteCodeResponse,
    MetricDiagnosticSchema,
    ObserverSettingsSchema,
    OfferRuleConfigSchema,
    OfferSchema,
    ScanningToggleSchema,
    SpendHistoryPoint,
    TelegramForumCutoverResponseSchema,
    TelegramPrimaryRecipientSchema,
    TelegramRecipientSchema,
    TelegramSettingsResponseSchema,
    TelegramSetTokenRequest,
    VisionSettingsSchema,
    VisionSettingsUpdateSchema,
    _normalize_offer_code_value,
    _offer_code_lookup_key,
)
from core.config import get_settings
from core.db import get_engine
from core.db.base import Base
from core.sentry import setup_sentry

# Инициализируем Sentry как можно раньше, до создания приложения
_s = get_settings()
setup_sentry(dsn=_s.sentry_dsn, environment=_s.sentry_environment)


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
    yield
    await engine.dispose()


app = FastAPI(title="FB Stop Bot API", version="0.1.0", lifespan=lifespan)

# CORS для React-фронтенда (только localhost-порты Vite и MiniApp)
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

# Включаем маршруты MiniApp
app.include_router(miniapp_router)

# Включаем роутеры для основного API
app.include_router(offers.router)
app.include_router(settings.router)
app.include_router(dashboard.router)
app.include_router(vision_telegram.router)


# ==========================================
# Health check endpoint
# ==========================================


class HealthResponse(BaseModel):
    status: str = "ok"
    db: str = "ok"


# Import these for backward compatibility - tests import them from here
from apps.api.routers.dashboard import (  # noqa: E402, F401
    _build_active_incident_schema,
    _build_campaign_stop_overrun_rows,
    _build_current_enable_tasks_query,
    _build_current_risk_reason_rows,
    _build_dashboard_performance_payload,
    _build_snapshot_base_budget_reference,
    _build_snapshot_metrics_json,
    _current_scan_cutoff,
    _is_disable_task_stale_for_manual_restart,
    _load_current_enable_recommendations,
    _serialize_enable_recommendation_event,
    _serialize_enable_task,
    create_disable_task,
    create_enable_task_from_recommendation,
    get_ad_timeline,
    get_chart_data,
    get_dashboard_performance,
    get_dashboard_stats,
    list_active_incidents,
    list_disable_tasks,
    list_enable_recommendations,
    list_enable_tasks,
    load_live_batch_bounds,
    retry_disable_task,
)
from apps.api.routers.settings import (  # noqa: E402, F401
    _activation_command,
    _create_forum_topics_if_needed,
    _mask_bot_token,
    _prepare_telegram_forum_cutover,
    _serialize_invite_response,
    _serialize_primary_recipient,
    _start_disable_process,
    _start_observer_process,
    _stop_disable_process,
    _stop_observer_process,
    get_telegram_settings,
    prepare_telegram_forum_cutover,
    restart_disable_worker,
    restart_observer,
    set_telegram_token,
    update_observer_settings,
)
from apps.api.routers.vision_telegram import (  # noqa: E402, F401
    create_invite_code,
    vision_reconnect,
)


@app.get("/health", response_model=HealthResponse)
async def health(db: AsyncSession = Depends(get_db)):
    """Проверяет доступность API и базы данных."""
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"
    return HealthResponse(status="ok", db=db_status)

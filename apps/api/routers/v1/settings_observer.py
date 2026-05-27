# -*- coding: utf-8 -*-
"""FastAPI роутер для настроек observer (settings_observer).

Endpoints под /api (благодаря auto-discovery с prefix="/api"):
- GET  /settings/observer          — читает ObserverConfig singleton
- PUT  /settings/observer          — обновляет все поля
- PATCH /settings/observer/scanning     — переключает is_scanning_enabled
- PATCH /settings/observer/auto-enable  — переключает auto_enable_recommendations
- POST /settings/observer/scan-now — публикует Redis сигнал fb_agent:observer:trigger
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import DepEngine, DepRedis
from apps.api.routers.v1.schemas.settings_observer import (
    AutoEnableToggleRequest,
    ObserverSettingsPutRequest,
    ObserverSettingsResponse,
    ScanningToggleRequest,
    ScanNowResponse,
)
from core.models.settings.observer_config import ObserverConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings/observer", tags=["settings"])

# Канал Redis для триггера scan-now.
_SCAN_NOW_CHANNEL = "fb_agent:observer:trigger"


async def _get_singleton(session: AsyncSession) -> ObserverConfig:
    """Возвращает singleton ObserverConfig, создаёт строку с дефолтами если её нет."""
    row = await session.scalar(
        select(ObserverConfig).where(ObserverConfig.singleton_key == "default")
    )
    if row is None:
        # Создаём запись с server-defaults (INSERT ... ON CONFLICT тоже работал бы,
        # но для singleton достаточно простого INSERT — race condition при первом запуске
        # маловероятен, а повторный CREATE бросит IntegrityError, которая rollback'нется
        # и в следующем запросе row будет найдена).
        row = ObserverConfig()
        session.add(row)
        await session.flush()
        await session.refresh(row)
    return row


@router.get("", response_model=ObserverSettingsResponse)
async def get_observer_settings(engine: DepEngine) -> ObserverSettingsResponse:
    """Возвращает текущий ObserverConfig singleton.

    Поля warning_percent_of_stop и WARNING-параметры возвращаются как null —
    они перенесены в OfferRule (per-offer). Фронт получает стабильный shape.
    """
    async with AsyncSession(engine) as session:
        cfg = await _get_singleton(session)
        return ObserverSettingsResponse(
            is_scanning_enabled=cfg.is_scanning_enabled,
            default_interval_seconds=cfg.interval_seconds,
            auto_enable_recommendations=cfg.auto_enable_recommendations,
        )


@router.put("", response_model=ObserverSettingsResponse)
async def put_observer_settings(
    body: ObserverSettingsPutRequest,
    engine: DepEngine,
) -> ObserverSettingsResponse:
    """Обновляет все поля ObserverConfig singleton.

    Валидация: default_interval_seconds от 30 до 600 (через Pydantic Field).
    """
    async with AsyncSession(engine) as session:
        cfg = await _get_singleton(session)
        cfg.is_scanning_enabled = body.is_scanning_enabled
        cfg.interval_seconds = body.default_interval_seconds
        cfg.auto_enable_recommendations = body.auto_enable_recommendations
        # Считываем значения ДО commit — после commit SQLAlchemy помечает
        # атрибуты expired, и их чтение триггерит lazy-load вне greenlet.
        result = ObserverSettingsResponse(
            is_scanning_enabled=cfg.is_scanning_enabled,
            default_interval_seconds=cfg.interval_seconds,
            auto_enable_recommendations=cfg.auto_enable_recommendations,
        )
        await session.commit()
        return result


@router.patch("/scanning", response_model=ObserverSettingsResponse)
async def patch_observer_scanning(
    body: ScanningToggleRequest,
    engine: DepEngine,
) -> ObserverSettingsResponse:
    """Переключает только is_scanning_enabled, не трогая остальные поля."""
    async with AsyncSession(engine) as session:
        cfg = await _get_singleton(session)
        cfg.is_scanning_enabled = body.enabled
        # Остальные поля не менялись — читаем из in-memory состояния до commit.
        result = ObserverSettingsResponse(
            is_scanning_enabled=cfg.is_scanning_enabled,
            default_interval_seconds=cfg.interval_seconds,
            auto_enable_recommendations=cfg.auto_enable_recommendations,
        )
        await session.commit()
        return result


@router.patch("/auto-enable", response_model=ObserverSettingsResponse)
async def patch_observer_auto_enable(
    body: AutoEnableToggleRequest,
    engine: DepEngine,
) -> ObserverSettingsResponse:
    """Переключает только auto_enable_recommendations (требует миграции 0003)."""
    async with AsyncSession(engine) as session:
        cfg = await _get_singleton(session)
        cfg.auto_enable_recommendations = body.enabled
        result = ObserverSettingsResponse(
            is_scanning_enabled=cfg.is_scanning_enabled,
            default_interval_seconds=cfg.interval_seconds,
            auto_enable_recommendations=cfg.auto_enable_recommendations,
        )
        await session.commit()
        return result


@router.post("/scan-now", response_model=ScanNowResponse)
async def post_scan_now(redis: DepRedis) -> ScanNowResponse:
    """Публикует Redis-событие fb_agent:observer:trigger для немедленного запуска scan.

    Subscriber в observer_worker — отдельная задача (не реализован здесь).
    Если Redis недоступен — возвращает 503.
    """
    payload = f'{{"requested_by": "api", "ts": "{datetime.now(UTC).isoformat()}"}}'
    try:
        await redis.publish(_SCAN_NOW_CHANNEL, payload)
    except Exception as exc:
        logger.error("Не удалось опубликовать событие scan-now в Redis: %s", exc)
        raise HTTPException(status_code=503, detail="Redis недоступен") from exc
    return ScanNowResponse(status="triggered")

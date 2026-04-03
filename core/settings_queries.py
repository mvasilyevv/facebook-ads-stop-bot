# -*- coding: utf-8 -*-
"""Общие запросы для ObserverSettings — единая точка доступа к singleton-настройкам."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_session_factory
from core.models import ObserverSettings

_SINGLETON_KEY = "default"


async def get_observer_settings(db: AsyncSession) -> ObserverSettings | None:
    """Загружает singleton ObserverSettings из переданной сессии (для API routers)."""
    result = await db.execute(
        select(ObserverSettings).where(ObserverSettings.singleton_key == _SINGLETON_KEY)
    )
    return result.scalar_one_or_none()


async def get_observer_settings_standalone() -> ObserverSettings | None:
    """Загружает singleton ObserverSettings через session factory (для workers/core)."""
    factory = get_session_factory()
    async with factory() as session:
        return await get_observer_settings(session)


async def get_or_create_observer_settings(db: AsyncSession) -> ObserverSettings:
    """Загружает или создаёт singleton ObserverSettings."""
    settings = await get_observer_settings(db)
    if settings is None:
        settings = ObserverSettings(singleton_key=_SINGLETON_KEY)
        db.add(settings)
        await db.flush()
    return settings

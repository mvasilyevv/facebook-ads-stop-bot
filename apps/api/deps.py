# -*- coding: utf-8 -*-
"""Зависимости FastAPI."""

from fastapi import Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.db import get_session_factory


async def get_db() -> AsyncSession:
    """FastAPI dependency: async сессия БД."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def verify_api_key(x_api_key: str = Header(default="")) -> None:
    """Проверка API-ключа. Пропускает если ключ не настроен (dev-режим)."""
    settings = get_settings()
    if not settings.api_key:
        # API-ключ не задан — пропускаем проверку (dev-режим)
        return
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Неверный API-ключ")

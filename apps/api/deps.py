# -*- coding: utf-8 -*-
"""Зависимости FastAPI."""

from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_session_factory


async def get_db() -> AsyncSession:
    """FastAPI dependency: async сессия БД."""
    factory = get_session_factory()
    async with factory() as session:
        yield session

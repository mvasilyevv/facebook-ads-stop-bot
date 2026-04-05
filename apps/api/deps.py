# -*- coding: utf-8 -*-
"""Зависимости FastAPI."""

import logging

from fastapi import Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.db import get_session_factory

logger = logging.getLogger(__name__)

# Флаг: предупреждение об отсутствии API-ключа выводится только один раз
_no_key_warned: bool = False


async def get_db() -> AsyncSession:
    """FastAPI dependency: async сессия БД."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def verify_api_key(request: Request, x_api_key: str = Header(default="")) -> None:
    """Проверка API-ключа.

    Если ключ не задан — разрешает только запросы с localhost.
    Для всех остальных адресов возвращает 403.
    """
    global _no_key_warned
    settings = get_settings()

    if not settings.api_key:
        # Предупреждение логируется только один раз за время жизни процесса
        if not _no_key_warned:
            logger.warning(
                "API_KEY не задан — API открыт только для localhost-запросов. "
                "Установите API_KEY в .env для защиты в production."
            )
            _no_key_warned = True

        # Определяем реальный IP клиента (учитываем прокси)
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()
        elif request.client:
            client_ip = request.client.host
        else:
            client_ip = ""

        _LOCALHOST_IPS = {"127.0.0.1", "::1", "localhost"}
        if client_ip not in _LOCALHOST_IPS:
            raise HTTPException(
                status_code=403,
                detail="API-ключ не задан — доступ разрешён только с localhost",
            )
        return

    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Неверный API-ключ")

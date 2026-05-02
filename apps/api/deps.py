# -*- coding: utf-8 -*-
"""Зависимости FastAPI."""

import logging

from fastapi import Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.tma import InvalidInitDataError, verify_session_token
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


def _get_client_ip(request: Request) -> str:
    """Определяет реальный IP клиента (учитываем прокси)."""
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return ""


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

        _LOCALHOST_IPS = {"127.0.0.1", "::1", "localhost"}
        if _get_client_ip(request) not in _LOCALHOST_IPS:
            raise HTTPException(
                status_code=403,
                detail="API-ключ не задан — доступ разрешён только с localhost",
            )
        return

    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Неверный API-ключ")


async def require_api_key_or_tma(
    request: Request,
    x_api_key: str = Header(default=""),
) -> None:
    """Зависимость: пускает если X-API-Key верен ИЛИ Bearer-токен TMA валиден.

    Используется для эндпоинтов, доступных и из dashboard-UI, и из mini-app.
    """
    settings = get_settings()

    # Быстрая проверка API-ключа
    if settings.api_key and x_api_key == settings.api_key:
        return

    # Попытка аутентификации через TMA Bearer-токен
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        bearer_token = auth_header[len("Bearer ") :]
        try:
            payload = verify_session_token(
                bearer_token, settings.api_key, settings.tma_session_ttl_seconds
            )
            request.state.tma_user_id = payload.get("telegram_user_id", "")
            return
        except InvalidInitDataError:
            pass

    # Если ключ не задан — разрешаем localhost (как verify_api_key)
    if not settings.api_key:
        _LOCALHOST_IPS = {"127.0.0.1", "::1", "localhost"}
        if _get_client_ip(request) in _LOCALHOST_IPS:
            return
        raise HTTPException(
            status_code=403,
            detail="API-ключ не задан — доступ разрешён только с localhost",
        )

    raise HTTPException(status_code=401, detail="Неверный API-ключ или токен")


async def require_tma_session(request: Request) -> None:
    """Зависимость: требует Bearer-токен TMA, кладёт tma_user_id в request.state."""
    settings = get_settings()
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Требуется авторизация TMA")

    bearer_token = auth_header[len("Bearer ") :]
    try:
        payload = verify_session_token(
            bearer_token, settings.api_key, settings.tma_session_ttl_seconds
        )
    except InvalidInitDataError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    request.state.tma_user_id = payload.get("telegram_user_id", "")

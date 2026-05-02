# -*- coding: utf-8 -*-
"""Роутер Telegram Mini App (TMA) — аутентификация и сессия."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_db, require_tma_session
from core.auth.tma import InvalidInitDataError, issue_session_token, validate_init_data
from core.config import get_settings
from core.models import TelegramRecipient, TelegramSettings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tma", tags=["tma"])


class AuthBody(BaseModel):
    """Тело запроса аутентификации через initData."""

    init_data: str


class AuthResponse(BaseModel):
    """Ответ с сессионным токеном."""

    token: str
    expires_in: int
    role: str


async def _load_telegram_settings(db: AsyncSession) -> TelegramSettings | None:
    """Загружает настройки Telegram (синглтон) из БД."""
    result = await db.execute(
        select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
    )
    return result.scalar_one_or_none()


@router.post("/auth", response_model=AuthResponse)
async def tma_auth(body: AuthBody, db: AsyncSession = Depends(get_db)) -> AuthResponse:
    """Аутентификация через Telegram initData.

    Проверяет подпись Telegram, определяет роль пользователя (owner/recipient)
    и выдаёт сессионный токен.
    """
    settings = get_settings()

    # Валидация initData по алгоритму HMAC Telegram WebApp
    try:
        data = validate_init_data(
            body.init_data,
            settings.telegram_bot_token,
            max_age_seconds=settings.tma_session_ttl_seconds,
        )
    except InvalidInitDataError as exc:
        logger.warning("Неверный initData при TMA-аутентификации: %s", exc)
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    user = data.get("user", {})
    tg_user_id = str(user.get("id", ""))
    if not tg_user_id:
        raise HTTPException(status_code=400, detail="Поле user.id отсутствует в initData")

    # Определяем роль пользователя
    tg_settings = await _load_telegram_settings(db)
    role: str | None = None

    if tg_settings and tg_settings.owner_telegram_user_id == tg_user_id:
        role = "owner"
    else:
        # Проверяем получателей
        result = await db.execute(
            select(TelegramRecipient).where(
                TelegramRecipient.telegram_user_id == tg_user_id,
                TelegramRecipient.is_active.is_(True),
            )
        )
        recipient = result.scalar_one_or_none()
        if recipient:
            role = "recipient"

    if role is None:
        raise HTTPException(status_code=403, detail="Доступ запрещён")

    token = issue_session_token(tg_user_id, settings.tma_session_ttl_seconds, settings.api_key)
    return AuthResponse(token=token, expires_in=settings.tma_session_ttl_seconds, role=role)


@router.get("/me")
async def tma_me(request: Request, _: None = Depends(require_tma_session)) -> dict:
    """Возвращает данные текущего TMA-сессии."""
    return {
        "telegram_user_id": request.state.tma_user_id,
        "role": getattr(request.state, "tma_role", None),
    }

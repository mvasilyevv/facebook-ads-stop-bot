# -*- coding: utf-8 -*-
"""Telegram Mini App: аутентификация + Bearer-guard (BL-15, Этап 0).

POST /api/tma/auth — принимает Telegram WebApp initData, валидирует по HMAC
(bot_token из telegram_config), сверяет user.id с telegram_recipients и выдаёт
подписанный сессионный токен (itsdangerous) + роль.

get_tma_principal — FastAPI-dependency: проверяет Bearer-токен на защищённых
TMA-endpoint'ах. Навешивается ТОЛЬКО на /tma/* (действия) — общие read-only
роутеры (/dashboard, /offers, ...) остаются открытыми, как для desktop-фронта.

Money/security: без валидного токена + активного recipient'а действия TMA
(disable/snooze/claim/draft-confirm в Этапе 2) недоступны.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from apps.api.deps import DepEngine, DepSettings
from core.auth.tma import (
    InvalidInitDataError,
    issue_session_token,
    validate_init_data,
    verify_session_token,
)
from core.config import Settings
from core.telegram.service import find_recipient_by_telegram_user_id, load_telegram_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tma", tags=["tma"])


class TmaAuthRequest(BaseModel):
    """Тело POST /tma/auth."""

    init_data: str = Field(..., description="Telegram WebApp initData (raw query string)")


class TmaAuthResponse(BaseModel):
    """Ответ авторизации: сессионный токен + роль recipient'а."""

    token: str
    role: str


class TmaMeResponse(BaseModel):
    """Кто я — для проверки сессии фронтом (под guard)."""

    telegram_user_id: int
    role: str


@dataclass(frozen=True)
class TmaPrincipal:
    """Авторизованный пользователь TMA (из проверенного Bearer-токена)."""

    telegram_user_id: int
    role: str
    chat_id: int

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


def _tma_secret(settings: Settings) -> str:
    """Секрет подписи токена: tma_session_secret или фолбэк на encryption_key."""
    return settings.tma_session_secret or settings.encryption_key


@router.post("/auth", response_model=TmaAuthResponse)
async def tma_auth(
    body: TmaAuthRequest,
    engine: DepEngine,
    settings: DepSettings,
) -> TmaAuthResponse:
    """Валидирует initData и выдаёт сессионный токен + роль.

    503 — Telegram/secret не настроены; 401 — initData невалиден/истёк;
    403 — пользователь не в списке доступа (нет активного recipient'а).
    """
    cfg = await load_telegram_config(engine)
    if cfg is None or not cfg.bot_token:
        raise HTTPException(status_code=503, detail="Telegram-бот не настроен")

    secret = _tma_secret(settings)
    if not secret:
        raise HTTPException(status_code=503, detail="TMA auth не настроен (нет secret)")

    try:
        data = validate_init_data(body.init_data, cfg.bot_token)
    except InvalidInitDataError as exc:
        raise HTTPException(status_code=401, detail=f"initData невалиден: {exc}") from exc

    user = data.get("user") or {}
    uid = user.get("id")
    if not uid:
        raise HTTPException(status_code=401, detail="В initData нет user")

    recipient = await find_recipient_by_telegram_user_id(engine, telegram_user_id=int(uid))
    if recipient is None:
        raise HTTPException(status_code=403, detail="Нет доступа — получи invite у владельца бота")

    token = issue_session_token(str(uid), settings.tma_session_ttl_seconds, secret)
    logger.info("TMA auth: user_id=%s role=%s", uid, recipient.role)
    return TmaAuthResponse(token=token, role=recipient.role)


async def get_tma_principal(
    request: Request,
    engine: DepEngine,
    settings: DepSettings,
) -> TmaPrincipal:
    """Dependency: извлекает и проверяет Bearer-токен TMA. 401/403/503 при отказе.

    Перепроверяет recipient'а в БД на КАЖДОМ запросе (а не доверяет токену) —
    отзыв доступа (revoked_at) срабатывает немедленно, не дожидаясь истечения токена.
    """
    auth_header = request.headers.get("Authorization") or ""
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Нужен Bearer-токен")
    token = auth_header[len("Bearer ") :].strip()

    secret = _tma_secret(settings)
    if not secret:
        raise HTTPException(status_code=503, detail="TMA auth не настроен")

    try:
        payload = verify_session_token(token, secret, settings.tma_session_ttl_seconds)
    except InvalidInitDataError as exc:
        raise HTTPException(status_code=401, detail="Токен невалиден или истёк") from exc

    uid = int(payload.get("telegram_user_id", 0) or 0)
    recipient = await find_recipient_by_telegram_user_id(engine, telegram_user_id=uid)
    if recipient is None:
        raise HTTPException(status_code=403, detail="Доступ отозван")

    return TmaPrincipal(
        telegram_user_id=uid,
        role=recipient.role,
        chat_id=recipient.chat_id,
    )


DepTmaPrincipal = Annotated[TmaPrincipal, Depends(get_tma_principal)]


@router.get("/me", response_model=TmaMeResponse)
async def tma_me(principal: DepTmaPrincipal) -> TmaMeResponse:
    """Проверка сессии: возвращает текущего пользователя (под Bearer-guard)."""
    return TmaMeResponse(telegram_user_id=principal.telegram_user_id, role=principal.role)


__all__ = [
    "DepTmaPrincipal",
    "TmaPrincipal",
    "get_tma_principal",
    "router",
]

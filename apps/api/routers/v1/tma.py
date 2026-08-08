# -*- coding: utf-8 -*-
"""Telegram Mini App: аутентификация + Bearer-guard (BL-15, Этап 0).

POST /api/tma/auth — принимает Telegram WebApp initData, валидирует по HMAC
(bot_token из telegram_config), сверяет user.id с telegram_recipients и выдаёт
подписанный сессионный токен (itsdangerous) + роль.

get_tma_principal — FastAPI-dependency для защищённых TMA endpoint'ов.
Ad detail и money-actions доступны через общий operator API, а не дублируются здесь.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from apps.api.deps import DepEngine, DepSettings
from core.auth.tma import (
    InvalidInitDataError,
    issue_session_token,
    validate_init_data,
    verify_session_token,
)
from core.config import Settings, reveal_secret
from core.telegram.navigation_tokens import consume_navigation_token
from core.telegram.service import (
    find_recipient_by_telegram_user_id,
    load_telegram_config,
    telegram_generation_is_authoritative,
)

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


class TmaNavigationResolveRequest(BaseModel):
    """Opaque one-time capability received through start_param or the Mini App URL."""

    token: str = Field(..., min_length=22, max_length=22, pattern=r"^[A-Za-z0-9_-]{22}$")


class TmaNavigationResolveResponse(BaseModel):
    target_kind: Literal["ad", "action", "incident"]
    target_id: str


@dataclass(frozen=True)
class TmaPrincipal:
    """Авторизованный пользователь TMA (из проверенного Bearer-токена)."""

    telegram_user_id: int
    role: str
    chat_id: int
    bot_generation: int

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


def _tma_secret(settings: Settings) -> str:
    """Return the dedicated TMA signing secret; never reuse encryption keys."""
    return reveal_secret(settings.tma_session_secret) if settings.tma_session_secret else ""


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
    if cfg is None or not cfg.bot_token or not cfg.webhook_ready:
        raise HTTPException(status_code=503, detail="Telegram-бот не настроен")

    secret = _tma_secret(settings)
    if not secret:
        raise HTTPException(status_code=503, detail="TMA auth не настроен (нет secret)")

    try:
        # M-15: узкое окно replay (дефолт 1ч вместо 24ч валидатора).
        data = validate_init_data(
            body.init_data,
            cfg.bot_token,
            max_age_seconds=settings.tma_init_data_max_age_seconds,
        )
    except InvalidInitDataError as exc:
        raise HTTPException(status_code=401, detail="initData невалиден") from exc

    user = data.get("user") or {}
    uid = user.get("id")
    if not uid:
        raise HTTPException(status_code=401, detail="В initData нет user")

    recipient = await find_recipient_by_telegram_user_id(engine, telegram_user_id=int(uid))
    if recipient is None:
        raise HTTPException(status_code=403, detail="Нет доступа — получи invite у владельца бота")

    token = issue_session_token(
        str(uid),
        settings.tma_session_ttl_seconds,
        secret,
        bot_generation=cfg.webhook_generation,
    )
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
    bot_generation = int(payload.get("bot_generation", 0) or 0)
    if not await telegram_generation_is_authoritative(
        engine,
        bot_generation=bot_generation,
    ):
        raise HTTPException(status_code=401, detail="Сессия относится к старой версии бота")
    recipient = await find_recipient_by_telegram_user_id(engine, telegram_user_id=uid)
    if recipient is None:
        raise HTTPException(status_code=403, detail="Доступ отозван")

    return TmaPrincipal(
        telegram_user_id=uid,
        role=recipient.role,
        chat_id=recipient.chat_id,
        bot_generation=bot_generation,
    )


DepTmaPrincipal = Annotated[TmaPrincipal, Depends(get_tma_principal)]


async def require_tma_owner(principal: DepTmaPrincipal) -> TmaPrincipal:
    """Fail closed for every TMA endpoint that can change operator state."""
    if not principal.is_owner:
        raise HTTPException(status_code=403, detail="Эта операция доступна только владельцу")
    return principal


DepTmaOwner = Annotated[TmaPrincipal, Depends(require_tma_owner)]


@router.get("/me", response_model=TmaMeResponse)
async def tma_me(principal: DepTmaPrincipal) -> TmaMeResponse:
    """Проверка сессии: возвращает текущего пользователя (под Bearer-guard)."""
    return TmaMeResponse(telegram_user_id=principal.telegram_user_id, role=principal.role)


@router.post("/navigation/resolve", response_model=TmaNavigationResolveResponse)
async def resolve_tma_navigation(
    body: TmaNavigationResolveRequest,
    principal: DepTmaPrincipal,
    engine: DepEngine,
) -> TmaNavigationResolveResponse:
    """Consume a recipient-bound navigation capability exactly once."""
    target = await consume_navigation_token(
        engine,
        raw_token=body.token,
        telegram_user_id=principal.telegram_user_id,
    )
    if target is None:
        # Deliberately do not disclose whether it was expired, consumed or belongs to someone else.
        raise HTTPException(status_code=404, detail="Ссылка недействительна или уже использована")
    return TmaNavigationResolveResponse(
        target_kind=target.kind,
        target_id=target.target_id,
    )

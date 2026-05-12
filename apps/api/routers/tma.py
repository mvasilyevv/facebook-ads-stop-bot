# -*- coding: utf-8 -*-
"""Роутер Telegram Mini App (TMA) — аутентификация и сессия."""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
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


# ---------------------------------------------------------------------------
# Pydantic-модели для ad-действий
# ---------------------------------------------------------------------------


class AdDetailResponse(BaseModel):
    """Зеркало AdDetailDTO."""

    fb_ad_id: str
    ad_name: str | None
    campaign_name: str | None
    adset_name: str | None
    state: str
    account_id: str | None
    metrics: dict
    snooze_until: datetime | None
    recent_alerts: list[dict]
    can_open_in_ads_manager: bool


class DisableRequest(BaseModel):
    """Тело запроса отключения объявления."""

    reason: str | None = None


class SnoozeRequest(BaseModel):
    """Тело запроса паузы объявления."""

    minutes: int = Field(ge=5, le=720, default=30)


# ---------------------------------------------------------------------------
# Вспомогательная функция: извлечь actor_username из state
# ---------------------------------------------------------------------------


def _get_actor_username(request: Request) -> str | None:
    """Пробует достать username из TMA-payload (если есть)."""
    return getattr(request.state, "tma_username", None)


# ---------------------------------------------------------------------------
# Эндпоинты объявлений
# ---------------------------------------------------------------------------


@router.get("/ads/{fb_ad_id}", response_model=AdDetailResponse)
async def tma_get_ad(
    fb_ad_id: str,
    request: Request,
    _: None = Depends(require_tma_session),
) -> AdDetailResponse:
    """Детальная информация об объявлении."""
    from core.ads.actions import AdNotFoundError, get_ad_detail

    try:
        dto = await get_ad_detail(fb_ad_id=fb_ad_id)
    except AdNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception:
        logger.exception("TMA get_ad_detail: ad=%s", fb_ad_id)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера") from None

    actor_id = request.state.tma_user_id
    logger.info("TMA action: get_detail ad=%s actor=%s", fb_ad_id, actor_id)
    return AdDetailResponse(**dto.__dict__)


@router.post("/ads/{fb_ad_id}/disable")
async def tma_disable_ad(
    fb_ad_id: str,
    body: DisableRequest,
    request: Request,
    _: None = Depends(require_tma_session),
) -> dict:
    """Отправить объявление на отключение."""
    from core.ads.actions import AdActionError, AdNotFoundError, disable_ad

    actor_id = request.state.tma_user_id
    logger.info("TMA action: disable ad=%s actor=%s", fb_ad_id, actor_id)
    try:
        result = await disable_ad(
            fb_ad_id=fb_ad_id,
            actor_telegram_user_id=actor_id,
            actor_username=_get_actor_username(request),
            reason=body.reason,
        )
    except AdNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AdActionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        logger.exception("TMA disable_ad: ad=%s", fb_ad_id)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера") from None

    return {"ok": True, **result}


@router.post("/ads/{fb_ad_id}/snooze")
async def tma_snooze_ad(
    fb_ad_id: str,
    body: SnoozeRequest,
    request: Request,
    _: None = Depends(require_tma_session),
) -> dict:
    """Поставить объявление на паузу (snooze)."""
    from core.ads.actions import AdActionError, AdNotFoundError, snooze_ad

    actor_id = request.state.tma_user_id
    logger.info("TMA action: snooze ad=%s actor=%s minutes=%s", fb_ad_id, actor_id, body.minutes)
    try:
        snoozed_until = await snooze_ad(
            fb_ad_id=fb_ad_id,
            minutes=body.minutes,
            actor_telegram_user_id=actor_id,
        )
    except AdNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AdActionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        logger.exception("TMA snooze_ad: ad=%s", fb_ad_id)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера") from None

    return {"ok": True, "snoozed_until": snoozed_until.isoformat()}


@router.post("/ads/{fb_ad_id}/claim")
async def tma_claim_ad(
    fb_ad_id: str,
    request: Request,
    _: None = Depends(require_tma_session),
) -> dict:
    """Взять объявление в работу (claim)."""
    from core.ads.actions import AdActionError, AdNotFoundError, claim_ad

    actor_id = request.state.tma_user_id
    logger.info("TMA action: claim ad=%s actor=%s", fb_ad_id, actor_id)
    try:
        await claim_ad(fb_ad_id=fb_ad_id, actor_telegram_user_id=actor_id)
    except AdNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AdActionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        logger.exception("TMA claim_ad: ad=%s", fb_ad_id)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера") from None

    return {"ok": True}

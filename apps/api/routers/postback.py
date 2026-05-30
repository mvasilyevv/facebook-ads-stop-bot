# -*- coding: utf-8 -*-
"""Inbound postback от AdSet.pro (см. META_INTEGRATION_PLAN.md §4.4 / Этап 6).

Endpoint:
- POST /api/v1/postback/adsetpro — принимает событие конверсии (FTD, hold, ...).

Аутентификация: header X-Postback-Secret.
- Если settings.adsetpro_postback_secret пуст в env → 503 "not configured".
  Это намеренно: лучше отдать «не настроен», чем тихо принимать неавторизованные
  постбэки.
- Если header не совпадает → 401.

После Волны 3 миграций (adsetpro_postback_events) endpoint INSERT'ит событие в БД
через core.adset_pro.ingest.ingest_postback с дедупом по (click_id, event_type)
внутри окна 24 часов.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.api.deps import get_engine, get_settings
from core.adset_pro import PostbackEvent
from core.adset_pro.credentials import resolve_adsetpro_postback_secret
from core.adset_pro.ingest import ingest_postback
from core.config import Settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/postback", tags=["postback"])


class AdsetProPostbackBody(BaseModel):
    """Pydantic-обёртка над PostbackEvent для FastAPI body validation.

    PostbackEvent — frozen dataclass, FastAPI его не валидирует напрямую, поэтому
    держим pydantic-модель отдельно. Все «лишние» поля попадут в raw через
    model_dump(), чтобы ничего не потерять.
    """

    click_id: str = Field(..., min_length=1)
    fb_ad_id: str | None = None
    event_type: str = Field(..., min_length=1)
    revenue: Decimal = Decimal(0)
    currency: str = "USD"

    model_config = {"extra": "allow"}


@router.post(
    "/adsetpro",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Принять postback от AdSet.pro",
)
async def receive_adsetpro_postback(
    body: AdsetProPostbackBody,
    x_postback_secret: str | None = Header(default=None, alias="X-Postback-Secret"),
    settings: Settings = Depends(get_settings),
    engine: AsyncEngine = Depends(get_engine),
) -> dict[str, Any]:
    """Принять postback, записать в БД с дедупом. 202 ACCEPTED + меткой результата."""
    # Секрет: БД (adsetpro_credentials) → фолбэк .env. Ротация без рестарта.
    expected_secret = await resolve_adsetpro_postback_secret(
        engine, fallback=settings.adsetpro_postback_secret
    )
    if not expected_secret:
        # Намеренно 503: пока секрет не задан, endpoint считается не настроенным —
        # принимать неавторизованные постбэки опаснее, чем вернуть «not configured».
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="adsetpro postback endpoint is not configured",
        )

    # secrets.compare_digest — constant-time сравнение, защищает от timing-attack
    # на публичном endpoint'е. Требует одинаковый тип у обоих аргументов; None header
    # подменяем пустой строкой, чтобы избежать TypeError.
    if not secrets.compare_digest(x_postback_secret or "", expected_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid postback secret",
        )

    # Собираем PostbackEvent — контракт core.adset_pro.schemas.
    raw_payload = body.model_dump()
    event = PostbackEvent(
        click_id=body.click_id,
        fb_ad_id=body.fb_ad_id,
        event_type=body.event_type,
        revenue=body.revenue,
        currency=body.currency,
        received_at=datetime.now(UTC),
        raw=raw_payload,
    )

    result = await ingest_postback(engine, event, signature_valid=True)

    logger.info(
        "adsetpro postback ingest: click_id=%s event_type=%s inserted=%s "
        "is_duplicate=%s fb_ad_fk=%s event_id=%s",
        event.click_id,
        event.event_type,
        result.inserted,
        result.is_duplicate,
        result.fb_ad_fk,
        result.event_id,
    )

    return {
        "received": True,
        "click_id": event.click_id,
        "inserted": result.inserted,
        "is_duplicate": result.is_duplicate,
        "event_id": result.event_id,
    }

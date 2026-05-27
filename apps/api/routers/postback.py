# -*- coding: utf-8 -*-
"""Inbound postback от AdSet.pro (см. META_INTEGRATION_PLAN.md §4.4 / Этап 6).

Endpoint:
- POST /api/v1/postback/adsetpro — принимает событие конверсии (FTD, hold, и т.п.)

Аутентификация: header X-Postback-Secret.
- Если settings.adsetpro_postback_secret пуст в env → 503 "not configured".
  Это намеренно: лучше отдать «не настроен», чем тихо принимать неавторизованные
  постбэки.
- Если header не совпадает → 401.

TODO(stage-6): после применения Волны 3 миграций (adsetpro_postback_events) —
заменить логирование на INSERT в БД с дедупом по click_id (либо body hash,
если click_id пустой). См. META_INTEGRATION_PLAN.md §5 Волна 3.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from apps.api.deps import get_settings
from core.adset_pro import PostbackEvent
from core.config import Settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/postback", tags=["postback"])


class AdsetProPostbackBody(BaseModel):
    """Pydantic-обёртка над PostbackEvent для FastAPI body validation.

    PostbackEvent — frozen dataclass, FastAPI его не валидирует напрямую,
    поэтому держим pydantic-модель отдельно. Все «лишние» поля попадут в raw
    через model_dump(), чтобы ничего не потерять.
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
) -> dict[str, Any]:
    """Принять postback. Возвращает 202 (логируется без записи в БД на этом этапе).

    TODO(stage-6): записать событие в adsetpro_postback_events после применения
    Волны 3 миграций. Идемпотентность — по click_id или по hash тела.
    """
    expected_secret = settings.adsetpro_postback_secret
    if not expected_secret:
        # Намеренно 503: пока секрет не задан, endpoint считается не настроенным —
        # принимать неавторизованные постбэки опаснее, чем вернуть «not configured».
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="adsetpro postback endpoint is not configured",
        )

    if x_postback_secret != expected_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid postback secret",
        )

    # Собираем PostbackEvent — это контракт core.adset_pro.schemas.
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

    # TODO(stage-6): INSERT в adsetpro_postback_events + дедуп по click_id.
    logger.info(
        "adsetpro postback received: click_id=%s fb_ad_id=%s event_type=%s revenue=%s %s",
        event.click_id,
        event.fb_ad_id,
        event.event_type,
        event.revenue,
        event.currency,
    )

    return {"received": True, "click_id": event.click_id}

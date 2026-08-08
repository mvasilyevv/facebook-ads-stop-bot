# -*- coding: utf-8 -*-
"""Telegram Bot API webhook: authenticate, persist, then acknowledge."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Response, status

from apps.api.deps import DepEngine, DepSettings
from core.config import reveal_secret
from core.telegram.schemas import TelegramWebhookUpdate
from core.telegram.update_inbox import (
    TelegramIngressUnavailableError,
    persist_telegram_update,
)

router = APIRouter(prefix="/v1/integrations/telegram", tags=["telegram"])


@router.post(
    "/webhook",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={
        401: {"description": "Invalid Telegram webhook secret"},
        503: {"description": "Telegram webhook secret is not configured"},
    },
)
async def receive_telegram_webhook(
    update: TelegramWebhookUpdate,
    engine: DepEngine,
    settings: DepSettings,
    bot_generation: Annotated[int, Query(ge=1)],
    secret_header: Annotated[
        str | None,
        Header(alias="X-Telegram-Bot-Api-Secret-Token"),
    ] = None,
) -> Response:
    """Return 204 only after the update is durable in PostgreSQL."""
    expected = reveal_secret(settings.telegram_webhook_secret).strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Telegram webhook is not configured")
    provided = secret_header or ""
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret")

    try:
        async with engine.begin() as conn:
            await persist_telegram_update(
                conn,
                update,
                bot_generation=bot_generation,
            )
    except TelegramIngressUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail="Telegram webhook generation is disabled or stale",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["receive_telegram_webhook", "router"]

# -*- coding: utf-8 -*-
"""Single production API for launching the protected Vision desktop."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response

from apps.api.deps import DepEngine, DepRedis, DepSettings
from apps.api.routers.v1.schemas.desktop import DesktopLaunchResponse
from apps.api.routers.v1.tma import get_tma_principal
from core.auth.desktop_access import (
    DesktopAccessError,
    build_desktop_launch_url,
    create_desktop_ticket,
)
from core.config import reveal_secret
from core.telegram.service import find_recipient_by_telegram_user_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/desktop", tags=["desktop"])

_PRODUCTION_ORIGIN = "https://app.adpulse.su"
_NO_STORE = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
}


async def _resolve_launch_identity(
    request: Request,
    engine: DepEngine,
    settings: DepSettings,
) -> tuple[int, str]:
    authorization = request.headers.get("authorization") or ""
    if authorization.startswith("Bearer "):
        # Reuse the canonical TMA verifier: signature, expiry and current
        # recipient state are checked again at the endpoint trust boundary.
        principal = await get_tma_principal(request, engine, settings)
        if not principal.is_owner:
            raise HTTPException(status_code=403, detail="Рабочий стол доступен только владельцу")
        return principal.telegram_user_id, "telegram_mini_app"

    if request.headers.get("origin") != _PRODUCTION_ORIGIN:
        raise HTTPException(status_code=403, detail="Недопустимый Origin")
    expected_key = reveal_secret(settings.api_key).strip()
    provided_key = request.headers.get("x-api-key") or ""
    if not expected_key:
        raise HTTPException(status_code=503, detail="API_KEY не сконфигурирован на сервере")
    if not provided_key or not secrets.compare_digest(provided_key, expected_key):
        raise HTTPException(status_code=401, detail="Требуется корректный X-API-Key")

    telegram_user_id = settings.desktop_owner_telegram_user_id
    if telegram_user_id <= 0:
        raise HTTPException(
            status_code=503,
            detail="DESKTOP_OWNER_TELEGRAM_USER_ID не сконфигурирован",
        )
    recipient = await find_recipient_by_telegram_user_id(engine, telegram_user_id=telegram_user_id)
    if recipient is None or recipient.role != "owner":
        raise HTTPException(status_code=403, detail="Доступ владельца отозван")
    return telegram_user_id, "web_panel"


@router.post(
    "/launch",
    response_model=DesktopLaunchResponse,
    responses={
        401: {"description": "Authentication failed"},
        403: {"description": "Not an active owner or invalid origin"},
        503: {"description": "Desktop access is not configured"},
    },
)
async def launch_desktop(
    request: Request,
    response: Response,
    engine: DepEngine,
    redis: DepRedis,
    settings: DepSettings,
) -> DesktopLaunchResponse:
    """Issue one single-use desktop URL. The request intentionally has no body."""
    response.headers.update(_NO_STORE)
    if settings.desktop_public_origin.rstrip("/") != _PRODUCTION_ORIGIN:
        raise HTTPException(
            status_code=503,
            detail="DESKTOP_PUBLIC_ORIGIN должен указывать на production-панель",
        )
    telegram_user_id, source = await _resolve_launch_identity(request, engine, settings)
    try:
        ticket, grant = await create_desktop_ticket(
            redis,
            telegram_user_id=telegram_user_id,
            source=source,
            ttl=settings.desktop_access_ticket_ttl_seconds,
        )
        url = build_desktop_launch_url(settings.desktop_public_origin, ticket)
    except DesktopAccessError as exc:
        logger.error("Desktop launch ticket could not be created: %s", exc)
        raise HTTPException(status_code=503, detail="Рабочий стол временно недоступен") from exc
    return DesktopLaunchResponse(
        url=url,
        expires_at=datetime.fromtimestamp(grant.expires_at, tz=timezone.utc),
    )


__all__ = ["router"]

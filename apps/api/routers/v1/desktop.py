# -*- coding: utf-8 -*-
"""Single production API for launching the protected Vision desktop."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response

from apps.api.deps import DepEngine, DepRedis, DepSettings
from apps.api.routers.panel_auth import resolve_panel_session
from apps.api.routers.v1.schemas.desktop import DesktopLaunchResponse, DesktopTransportsResponse
from apps.api.routers.v1.tma import get_tma_principal
from core.auth.desktop_access import (
    DesktopAccessError,
    build_desktop_launch_url,
    create_desktop_ticket,
)
from core.config import reveal_secret

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/desktop", tags=["desktop"])

_PANEL_PRODUCTION_ORIGIN = "https://app.adpulse.su"
_DESKTOP_PRODUCTION_ORIGIN = "https://desktop.adpulse.su"
_NO_STORE = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
}


async def _resolve_launch_identity(
    request: Request,
    engine: DepEngine,
    redis: DepRedis,
    settings: DepSettings,
    *,
    require_origin: bool = True,
) -> tuple[int, str]:
    authorization = request.headers.get("authorization") or ""
    if authorization.startswith("Bearer "):
        # Reuse the canonical TMA verifier: signature, expiry and current
        # recipient state are checked again at the endpoint trust boundary.
        principal = await get_tma_principal(request, engine, settings)
        if not principal.is_owner:
            raise HTTPException(status_code=403, detail="Рабочий стол доступен только владельцу")
        return principal.telegram_user_id, "telegram_mini_app"

    if require_origin and request.headers.get("origin") != _PANEL_PRODUCTION_ORIGIN:
        raise HTTPException(status_code=403, detail="Недопустимый Origin")
    expected_key = reveal_secret(settings.api_key).strip()
    provided_key = request.headers.get("x-api-key") or ""
    if not expected_key:
        raise HTTPException(status_code=503, detail="API_KEY не сконфигурирован на сервере")
    if not provided_key or not secrets.compare_digest(provided_key, expected_key):
        raise HTTPException(status_code=401, detail="Требуется корректный X-API-Key")

    resolved = await resolve_panel_session(request, engine, redis, settings)
    if resolved is None:
        raise HTTPException(status_code=401, detail="Требуется вход через Telegram")
    _, session = resolved
    return session.telegram_user_id, "web_panel"


def _desktop_origin(settings: DepSettings) -> tuple[str, str]:
    origin = settings.desktop_public_origin.rstrip("/")
    if origin != _DESKTOP_PRODUCTION_ORIGIN:
        raise HTTPException(
            status_code=503,
            detail="DESKTOP_PUBLIC_ORIGIN должен указывать на production desktop hostname",
        )
    hostname = urlsplit(origin).hostname
    if not hostname:
        raise HTTPException(status_code=503, detail="Desktop hostname не настроен")
    return origin, hostname


@router.get("/transports", response_model=DesktopTransportsResponse)
async def list_desktop_transports(
    request: Request,
    response: Response,
    engine: DepEngine,
    redis: DepRedis,
    settings: DepSettings,
) -> DesktopTransportsResponse:
    """Return owner-visible transport choices without issuing a ticket."""
    response.headers.update(_NO_STORE)
    await _resolve_launch_identity(request, engine, redis, settings, require_origin=False)
    return DesktopTransportsResponse(active="kasm", available=["kasm"])


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
    transport: Literal["active", "kasm"] = "active",
) -> DesktopLaunchResponse:
    """Issue one single-use desktop URL. The request intentionally has no body."""
    response.headers.update(_NO_STORE)
    public_origin, expected_hostname = _desktop_origin(settings)
    telegram_user_id, source = await _resolve_launch_identity(request, engine, redis, settings)
    try:
        ticket, grant = await create_desktop_ticket(
            redis,
            telegram_user_id=telegram_user_id,
            source=source,
            expected_hostname=expected_hostname,
            ttl=settings.desktop_access_ticket_ttl_seconds,
        )
        url = build_desktop_launch_url(public_origin, ticket)
    except DesktopAccessError as exc:
        logger.error("Desktop launch ticket could not be created: %s", exc)
        raise HTTPException(status_code=503, detail="Рабочий стол временно недоступен") from exc
    return DesktopLaunchResponse(
        url=url,
        expires_at=datetime.fromtimestamp(grant.expires_at, tz=timezone.utc),
        transport="kasm",
    )


__all__ = ["router"]

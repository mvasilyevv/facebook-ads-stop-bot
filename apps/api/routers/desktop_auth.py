# -*- coding: utf-8 -*-
"""Revocable owner-session gate for the browser-based Vision desktop."""

from __future__ import annotations

import html
import logging
import secrets
import time

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from apps.api.deps import DepEngine, DepRedis, DepSettings
from core.auth.desktop_access import (
    DESKTOP_SESSION_COOKIE,
    DesktopAccessError,
    DesktopSession,
    build_desktop_launch_url,
    build_guacamole_auth_data,
    build_guacamole_connect_url,
    consume_desktop_ticket,
    create_desktop_session,
    create_desktop_ticket,
    delete_desktop_session,
    load_desktop_session,
    mark_desktop_owner_checked,
)
from core.config import Settings, reveal_secret
from core.telegram.service import (
    Recipient,
    find_recipient_by_telegram_user_id,
    load_owner_recipients,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/desktop-auth", tags=["desktop-auth"])

_NO_STORE = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
}
_PANEL_DESKTOP_PAGE = "https://app.adpulse.su/remote-desktop"


def _set_desktop_cookie(response: Response, token: str, max_age: int) -> None:
    response.set_cookie(
        DESKTOP_SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def _guacamole_location(settings: Settings, telegram_user_id: int) -> str:
    data = build_guacamole_auth_data(
        reveal_secret(settings.desktop_guacamole_json_secret),
        telegram_user_id=telegram_user_id,
        vnc_password=reveal_secret(settings.desktop_vnc_password),
        ttl=settings.desktop_guacamole_token_ttl_seconds,
    )
    return build_guacamole_connect_url(data)


def _valid_recovery_key(settings: Settings, presented: str | None) -> bool:
    expected = reveal_secret(settings.api_key).strip()
    return bool(expected and presented and secrets.compare_digest(presented, expected))


async def _load_single_owner(engine: DepEngine) -> Recipient | None:
    owners = await load_owner_recipients(engine)
    if len(owners) != 1:
        logger.error("Desktop access refused: active owner count=%d", len(owners))
        return None
    return owners[0]


def _desktop_error(message: str, status_code: int = 403) -> HTMLResponse:
    safe_message = html.escape(message)
    document = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><title>Desktop access · AdPulse</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0d10;
color:#f3f5f7;font-family:Inter,system-ui,sans-serif}}main{{width:min(520px,calc(100% - 40px));
border:1px solid #30353c;padding:28px;background:#111418}}p{{color:#9aa3ad;line-height:1.55}}
a{{display:inline-block;margin-top:10px;color:#0b0d10;background:#f3f5f7;padding:12px 16px;
text-decoration:none;font-weight:700}}</style></head><body><main><h1>Доступ не выдан</h1>
<p>{safe_message}</p><a href="https://app.adpulse.su/remote-desktop">Вернуться в AdPulse</a>
</main></body></html>"""
    return HTMLResponse(
        document,
        status_code=status_code,
        headers={
            **_NO_STORE,
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
                "frame-ancestors 'none'"
            ),
            "X-Frame-Options": "DENY",
        },
    )


async def _resolve_desktop_session(
    request: Request,
    engine: DepEngine,
    redis: DepRedis,
    settings: DepSettings,
) -> tuple[str, DesktopSession] | None:
    token = request.cookies.get(DESKTOP_SESSION_COOKIE)
    session = await load_desktop_session(redis, token)
    if session is None or token is None:
        return None
    now = int(time.time())
    if now - session.owner_checked_at >= settings.desktop_access_owner_recheck_seconds:
        recipient = await find_recipient_by_telegram_user_id(
            engine, telegram_user_id=session.telegram_user_id
        )
        if recipient is None or recipient.role != "owner":
            await delete_desktop_session(redis, token)
            return None
        session = await mark_desktop_owner_checked(redis, token, session, now)
    return token, session


@router.get("/redeem", include_in_schema=False)
async def redeem_desktop_ticket(
    engine: DepEngine,
    redis: DepRedis,
    settings: DepSettings,
    ticket: str = Query(default=""),
) -> Response:
    try:
        grant = await consume_desktop_ticket(redis, ticket)
        recipient = await find_recipient_by_telegram_user_id(
            engine, telegram_user_id=grant.telegram_user_id
        )
        if recipient is None or recipient.role != "owner":
            raise DesktopAccessError("Telegram-аккаунт больше не является владельцем")
        token, _ = await create_desktop_session(
            redis,
            telegram_user_id=grant.telegram_user_id,
            source=grant.source,
            ttl=settings.desktop_access_session_ttl_seconds,
        )
    except DesktopAccessError as exc:
        return _desktop_error(str(exc))
    response = RedirectResponse("/desktop-auth/connect", status_code=303, headers=_NO_STORE)
    _set_desktop_cookie(response, token, settings.desktop_access_session_ttl_seconds)
    return response


@router.get("/connect", include_in_schema=False)
async def connect_desktop(
    request: Request,
    engine: DepEngine,
    redis: DepRedis,
    settings: DepSettings,
) -> Response:
    """Exchange the revocable desktop cookie for a short Guacamole grant."""
    resolved = await _resolve_desktop_session(request, engine, redis, settings)
    if resolved is None:
        response = RedirectResponse(_PANEL_DESKTOP_PAGE, status_code=303, headers=_NO_STORE)
        response.delete_cookie(DESKTOP_SESSION_COOKIE, path="/", secure=True, httponly=True)
        return response
    _, session = resolved
    try:
        location = _guacamole_location(settings, session.telegram_user_id)
    except DesktopAccessError as exc:
        logger.error("Guacamole desktop launch is not configured: %s", exc)
        return _desktop_error("Мобильный рабочий стол временно не настроен", 503)
    return RedirectResponse(location, status_code=303, headers=_NO_STORE)


@router.get("/verify", include_in_schema=False)
async def verify_desktop_session(
    request: Request,
    engine: DepEngine,
    redis: DepRedis,
    settings: DepSettings,
) -> Response:
    resolved = await _resolve_desktop_session(request, engine, redis, settings)
    if resolved is not None:
        _, session = resolved
        return Response(
            status_code=200,
            headers={
                **_NO_STORE,
                "X-Desktop-Telegram-User-Id": str(session.telegram_user_id),
                "X-Desktop-Role": "owner",
            },
        )
    response = RedirectResponse(_PANEL_DESKTOP_PAGE, status_code=303, headers=_NO_STORE)
    response.delete_cookie(DESKTOP_SESSION_COOKIE, path="/", secure=True, httponly=True)
    return response


@router.get("/logout", include_in_schema=False)
async def logout_desktop(request: Request, redis: DepRedis) -> Response:
    await delete_desktop_session(redis, request.cookies.get(DESKTOP_SESSION_COOKIE))
    response = RedirectResponse(_PANEL_DESKTOP_PAGE, status_code=303, headers=_NO_STORE)
    response.delete_cookie(DESKTOP_SESSION_COOKIE, path="/", secure=True, httponly=True)
    return response


@router.get("/recovery", include_in_schema=False)
async def recover_desktop_session(
    engine: DepEngine,
    redis: DepRedis,
    settings: DepSettings,
    x_panel_recovery_key: str | None = Header(default=None),
) -> Response:
    if not _valid_recovery_key(settings, x_panel_recovery_key):
        return Response(status_code=404)
    owner = await _load_single_owner(engine)
    if owner is None:
        return _desktop_error("Аварийный вход недоступен: состав владельцев неоднозначен", 503)
    token, _ = await create_desktop_session(
        redis,
        telegram_user_id=owner.telegram_user_id,
        source="basic_recovery",
        ttl=settings.desktop_access_recovery_ttl_seconds,
    )
    response = RedirectResponse("/desktop-auth/connect", status_code=303, headers=_NO_STORE)
    _set_desktop_cookie(response, token, settings.desktop_access_recovery_ttl_seconds)
    return response


@router.get("/launch-recovery", include_in_schema=False)
async def launch_desktop_from_panel(
    engine: DepEngine,
    redis: DepRedis,
    settings: DepSettings,
    x_panel_recovery_key: str | None = Header(default=None),
) -> Response:
    """Turn an authenticated panel request into a one-time cross-host ticket."""
    if not _valid_recovery_key(settings, x_panel_recovery_key):
        return Response(status_code=404)
    owner = await _load_single_owner(engine)
    if owner is None:
        return _desktop_error("Подключение недоступно: состав владельцев неоднозначен", 503)
    try:
        ticket, _ = await create_desktop_ticket(
            redis,
            telegram_user_id=owner.telegram_user_id,
            source="panel_basic_auth",
            ttl=settings.desktop_access_ticket_ttl_seconds,
        )
        location = build_desktop_launch_url(settings.desktop_access_base_url, ticket)
    except DesktopAccessError as exc:
        logger.error("Desktop launch ticket could not be created: %s", exc)
        return _desktop_error("Рабочий стол временно не настроен", 503)
    return RedirectResponse(location, status_code=303, headers=_NO_STORE)


@router.post("/launch-url-recovery", include_in_schema=False)
async def create_desktop_launch_url_from_panel(
    engine: DepEngine,
    redis: DepRedis,
    settings: DepSettings,
    x_panel_recovery_key: str | None = Header(default=None),
) -> Response:
    """Issue a one-time desktop URL to an authenticated same-origin panel request."""
    if not _valid_recovery_key(settings, x_panel_recovery_key):
        return Response(status_code=404)
    owner = await _load_single_owner(engine)
    if owner is None:
        return JSONResponse(
            {"detail": "Подключение недоступно: состав владельцев неоднозначен"},
            status_code=503,
            headers=_NO_STORE,
        )
    try:
        ticket, _ = await create_desktop_ticket(
            redis,
            telegram_user_id=owner.telegram_user_id,
            source="panel_basic_auth",
            ttl=settings.desktop_access_ticket_ttl_seconds,
        )
        location = build_desktop_launch_url(settings.desktop_access_base_url, ticket)
    except DesktopAccessError as exc:
        logger.error("Desktop launch ticket could not be created: %s", exc)
        return JSONResponse(
            {"detail": "Рабочий стол временно не настроен"},
            status_code=503,
            headers=_NO_STORE,
        )
    return JSONResponse({"url": location}, headers=_NO_STORE)


__all__ = ["router"]

# -*- coding: utf-8 -*-
"""Owner-only Telegram OIDC entry point and panel forward-auth verifier."""

from __future__ import annotations

import html
import json
import logging
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from apps.api.deps import DepEngine, DepSettings
from core.auth.panel_access import (
    PANEL_SESSION_COOKIE,
    TELEGRAM_JWKS_URL,
    TELEGRAM_TOKEN_URL,
    PanelAuthError,
    PanelSession,
    TelegramSigningKeyNotFound,
    consume_oidc_attempt,
    consume_panel_ticket,
    create_oidc_authorization,
    create_panel_session,
    create_panel_ticket,
    delete_panel_session,
    load_panel_session,
    safe_return_to,
    save_oidc_attempt,
    verify_telegram_id_token,
)
from core.config import reveal_secret
from core.telegram.service import find_recipient_by_telegram_user_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["panel-auth"])
_PANEL_TICKET_COOKIE = "__Host-adpulse_panel_ticket_v1"

_NO_STORE = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
}


def _page(*, title: str, lead: str, body: str, status_code: int = 200) -> HTMLResponse:
    document = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><title>{html.escape(title)} · AdPulse</title>
<style>:root{{color-scheme:dark;font-family:Inter,system-ui,sans-serif}}*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;color:#f3f5f7;background:#0b0d10}}
main{{width:min(560px,calc(100% - 36px));border:1px solid #30353c;background:#111418;padding:clamp(28px,6vw,52px)}}
.mark{{color:#ff765f;font:12px ui-monospace,monospace;letter-spacing:.14em;text-transform:uppercase}}
h1{{margin:18px 0 10px;font-size:clamp(34px,7vw,58px);letter-spacing:-.05em;line-height:.98}}
.lead,.note{{color:#99a2ad;line-height:1.55}}.button{{margin-top:24px;display:flex;justify-content:space-between;
padding:17px 19px;color:#0b0d10;background:#f4f6f8;text-decoration:none;font-weight:750}}
.error{{margin-top:24px;padding:16px;border:1px solid #5c2d27;background:#1b1110;color:#ff9d8f}}
a{{color:inherit}}</style></head><body><main><div class="mark">AdPulse Control</div>
<h1>{html.escape(title)}</h1><p class="lead">{html.escape(lead)}</p>{body}</main></body></html>"""
    return HTMLResponse(
        document,
        status_code=status_code,
        headers={
            **_NO_STORE,
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
                "frame-ancestors 'none'; form-action 'self'"
            ),
            "X-Frame-Options": "DENY",
        },
    )


def _login_page(return_to: str, *, configured: bool) -> HTMLResponse:
    if configured:
        start_url = "/auth/telegram/start?" + urlencode({"return_to": return_to})
        body = (
            f'<a class="button" href="{html.escape(start_url, quote=True)}">'
            "<span>Войти через Telegram</span><span>→</span></a>"
            '<p class="note">Доступ получит только активный владелец. '
            "Пароль панели больше не требуется.</p>"
        )
    else:
        body = '<div class="error">Telegram Login ещё не подключён администратором.</div>'
    return _page(
        title="Вход владельца",
        lead="Подтвердите личность через официальный Telegram Login.",
        body=body,
    )


def _auth_error(message: str, status_code: int = 400) -> HTMLResponse:
    return _page(
        title="Вход не завершён",
        lead="Панель не выдала сессию. Никакие настройки не были изменены.",
        body=(
            f'<div class="error">{html.escape(message)}</div>'
            '<p class="note"><a href="/auth/login">Вернуться ко входу</a></p>'
        ),
        status_code=status_code,
    )


def _set_session_cookie(response: Response, token: str, max_age: int) -> None:
    response.set_cookie(
        PANEL_SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        PANEL_SESSION_COOKIE,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )


def _set_ticket_cookie(response: Response, ticket: str, max_age: int) -> None:
    response.set_cookie(
        _PANEL_TICKET_COOKIE,
        ticket,
        max_age=max_age,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )


def _clear_ticket_cookie(response: Response) -> None:
    response.delete_cookie(
        _PANEL_TICKET_COOKIE,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )


async def resolve_panel_session(
    request: Request,
    engine: DepEngine,
    settings: DepSettings,
) -> tuple[str, PanelSession] | None:
    """Resolve a session only while its Telegram recipient is an active owner."""
    del settings
    token = request.cookies.get(PANEL_SESSION_COOKIE)
    session = await load_panel_session(engine, token)
    if session is None or token is None:
        return None
    return token, session


async def _exchange_code(
    *, client_id: str, client_secret: str, redirect_uri: str, code: str, verifier: str
) -> str:
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.post(
                TELEGRAM_TOKEN_URL,
                auth=httpx.BasicAuth(client_id, client_secret),
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "code_verifier": verifier,
                },
            )
    except httpx.HTTPError as exc:
        raise PanelAuthError("Telegram Login временно недоступен") from exc
    if response.status_code != 200:
        logger.warning("Telegram token exchange failed (status=%d)", response.status_code)
        raise PanelAuthError("Telegram не подтвердил вход")
    try:
        token = str(response.json()["id_token"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PanelAuthError("Telegram не вернул ID token") from exc
    if not token:
        raise PanelAuthError("Telegram не вернул ID token")
    return token


async def _load_jwks(*, force_refresh: bool = False) -> dict:
    # ``force_refresh`` documents the single key-rotation retry at the caller.
    # JWKS is intentionally fetched without Redis: panel authentication must
    # remain available when the optional cache/wakeup service is unavailable.
    del force_refresh
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(TELEGRAM_JWKS_URL)
    except httpx.HTTPError as exc:
        raise PanelAuthError("Не удалось получить ключи Telegram") from exc
    if response.status_code != 200:
        raise PanelAuthError("Не удалось получить ключи Telegram")
    try:
        jwks = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise PanelAuthError("Telegram вернул некорректные ключи") from exc
    if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
        raise PanelAuthError("Telegram вернул некорректные ключи")
    return jwks


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login(settings: DepSettings, return_to: str | None = Query(default=None)) -> HTMLResponse:
    configured = bool(
        settings.telegram_oidc_client_id.strip()
        and reveal_secret(settings.telegram_oidc_client_secret).strip()
    )
    return _login_page(safe_return_to(return_to), configured=configured)


@router.get("/telegram/start", include_in_schema=False)
async def telegram_start(
    engine: DepEngine,
    settings: DepSettings,
    return_to: str | None = Query(default=None),
) -> Response:
    try:
        if not reveal_secret(settings.telegram_oidc_client_secret).strip():
            raise PanelAuthError("Telegram Login пока не настроен")
        state, url, attempt = create_oidc_authorization(
            client_id=settings.telegram_oidc_client_id,
            redirect_uri=settings.telegram_oidc_redirect_uri,
            return_to=return_to,
        )
        await save_oidc_attempt(engine, state, attempt, settings.panel_auth_state_ttl_seconds)
    except PanelAuthError:
        return _auth_error("Telegram Login временно недоступен", status_code=503)
    return RedirectResponse(url, status_code=303, headers=_NO_STORE)


@router.get("/telegram/callback", include_in_schema=False)
async def telegram_callback(
    engine: DepEngine,
    settings: DepSettings,
    state: str = Query(default=""),
    code: str = Query(default=""),
    error: str | None = Query(default=None),
) -> Response:
    try:
        attempt = await consume_oidc_attempt(engine, state)
        if error:
            raise PanelAuthError("Вход отменён в Telegram")
        if not code:
            raise PanelAuthError("Telegram не вернул код авторизации")
        id_token = await _exchange_code(
            client_id=settings.telegram_oidc_client_id,
            client_secret=reveal_secret(settings.telegram_oidc_client_secret),
            redirect_uri=settings.telegram_oidc_redirect_uri,
            code=code,
            verifier=attempt.code_verifier,
        )
        jwks = await _load_jwks()
        try:
            claims = verify_telegram_id_token(
                id_token,
                jwks=jwks,
                client_id=settings.telegram_oidc_client_id,
                nonce=attempt.nonce,
            )
        except TelegramSigningKeyNotFound:
            # Key rotation: refresh once, then fail closed if the kid is still unknown.
            claims = verify_telegram_id_token(
                id_token,
                jwks=await _load_jwks(force_refresh=True),
                client_id=settings.telegram_oidc_client_id,
                nonce=attempt.nonce,
            )
        telegram_user_id = int(claims["id"])
        recipient = await find_recipient_by_telegram_user_id(
            engine, telegram_user_id=telegram_user_id
        )
        if recipient is None or recipient.role != "owner":
            logger.warning("Panel Telegram login denied (telegram_user_id=%d)", telegram_user_id)
            raise PanelAuthError("Этот Telegram-аккаунт не назначен владельцем панели")
        ticket, _ = await create_panel_ticket(
            engine,
            telegram_user_id=telegram_user_id,
            source="telegram_oidc",
            return_to=attempt.return_to,
            ttl=settings.panel_auth_ticket_ttl_seconds,
        )
    except PanelAuthError:
        return _auth_error("Telegram Login не подтверждён", status_code=403)
    response = RedirectResponse("/auth/redeem", status_code=303, headers=_NO_STORE)
    _set_ticket_cookie(response, ticket, settings.panel_auth_ticket_ttl_seconds)
    return response


@router.get("/redeem", include_in_schema=False)
async def redeem(
    request: Request,
    engine: DepEngine,
    settings: DepSettings,
) -> Response:
    ticket = request.cookies.get(_PANEL_TICKET_COOKIE, "")
    try:
        grant = await consume_panel_ticket(engine, ticket)
        recipient = await find_recipient_by_telegram_user_id(
            engine, telegram_user_id=grant.telegram_user_id
        )
        if recipient is None or recipient.role != "owner":
            raise PanelAuthError("Доступ владельца был отозван")
        token, _ = await create_panel_session(
            engine,
            telegram_user_id=grant.telegram_user_id,
            role="owner",
            source=grant.source,
            ttl=settings.panel_auth_session_ttl_seconds,
        )
    except PanelAuthError:
        response = _auth_error(
            "Ссылка входа недействительна или уже использована",
            status_code=403,
        )
        _clear_ticket_cookie(response)
        return response
    response = RedirectResponse(grant.return_to, status_code=303, headers=_NO_STORE)
    _clear_ticket_cookie(response)
    _set_session_cookie(response, token, settings.panel_auth_session_ttl_seconds)
    return response


@router.get("/verify", include_in_schema=False)
async def verify(
    request: Request,
    engine: DepEngine,
    settings: DepSettings,
) -> Response:
    resolved = await resolve_panel_session(request, engine, settings)
    if resolved is not None:
        _token, session = resolved
        return Response(
            status_code=200,
            headers={
                **_NO_STORE,
                # Caddy copies this server-derived identity onto the original
                # request after stripping the client-provided header from the
                # verifier subrequest.  It is attribution, not authorization.
                "X-Verified-Operator-Principal": (f"panel:{session.telegram_user_id}"),
            },
        )

    forwarded_uri = safe_return_to(request.headers.get("x-forwarded-uri"))
    login_url = "/auth/login?" + urlencode({"return_to": forwarded_uri})
    if forwarded_uri.startswith(("/api/", "/ws/")):
        response: Response = JSONResponse(
            status_code=401,
            content={"detail": "Требуется вход через Telegram", "login_url": login_url},
            headers={**_NO_STORE, "X-Auth-Login-Url": login_url},
        )
    else:
        response = RedirectResponse(login_url, status_code=303, headers=_NO_STORE)
    _clear_session_cookie(response)
    return response


@router.get("/logout", include_in_schema=False)
async def logout(request: Request, engine: DepEngine) -> Response:
    await delete_panel_session(engine, request.cookies.get(PANEL_SESSION_COOKIE))
    response = RedirectResponse("/auth/login", status_code=303, headers=_NO_STORE)
    _clear_session_cookie(response)
    return response


__all__ = ["resolve_panel_session", "router"]

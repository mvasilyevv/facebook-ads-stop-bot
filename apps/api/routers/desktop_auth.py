# -*- coding: utf-8 -*-
"""Technical endpoints for the production Vision desktop session gate."""

from __future__ import annotations

import hashlib
import html
import logging
import stat
import time
from asyncio import Lock
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Protocol

import httpx
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from apps.api.deps import DepEngine, DepRedis, DepSettings
from core.auth.desktop_access import (
    DESKTOP_PRINCIPAL,
    DESKTOP_SESSION_COOKIE,
    DesktopAccessError,
    DesktopSession,
    consume_desktop_ticket,
    create_desktop_session,
    delete_desktop_session,
    load_desktop_session,
)
from core.config import Settings
from core.telegram.service import find_recipient_by_telegram_user_id

logger = logging.getLogger(__name__)
router = APIRouter(tags=["desktop-auth"])

_NO_STORE = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
}
_PANEL_DESKTOP_PAGE = "https://app.adpulse.su/remote-desktop"


class DesktopReadinessProbe(Protocol):
    """Injected boundary for probing the isolated desktop runtime."""

    async def check(self, settings: Settings) -> dict[str, bool]: ...


class DesktopReadinessCredentialError(ValueError):
    """Committed desktop readiness credential state is absent or unsafe."""


def _desktop_readiness_credentials(settings: Settings) -> tuple[str, str, str]:
    path = Path(settings.desktop_readiness_credentials_path)
    root = path.parent
    try:
        resolved = path.resolve(strict=True)
        file_stat = resolved.lstat()
    except OSError as exc:
        raise DesktopReadinessCredentialError("credential state is unavailable") from exc
    if (
        resolved.parent != root / "states"
        or not stat.S_ISREG(file_stat.st_mode)
        or stat.S_IMODE(file_stat.st_mode) != 0o600
    ):
        raise DesktopReadinessCredentialError("credential state violates its contract")
    try:
        content = resolved.read_bytes()
        lines = content.decode("utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise DesktopReadinessCredentialError("credential state cannot be read") from exc

    def exactly_one(key: str) -> str:
        prefix = f"{key}="
        values = [line[len(prefix) :] for line in lines if line.startswith(prefix)]
        if len(values) != 1 or not values[0]:
            raise DesktopReadinessCredentialError(f"{key} is missing")
        return values[0]

    username = exactly_one("DESKTOP_KASM_SERVICE_USER").strip()
    password = exactly_one("DESKTOP_KASM_SERVICE_PASSWORD")
    if not username or ":" in username or len(password) < 16:
        raise DesktopReadinessCredentialError("credential state values are invalid")
    revision = hashlib.sha256(content).hexdigest()
    return username, password, revision


def _desktop_readiness_revision(settings: Settings) -> str:
    try:
        return _desktop_readiness_credentials(settings)[2]
    except DesktopReadinessCredentialError:
        return "unavailable"


class NetworkDesktopReadinessProbe:
    """Fail-closed Kasm HTTP and BasicAuth readiness check."""

    async def check(self, settings: Settings) -> dict[str, bool]:
        """Require both the Kasm auth challenge and authenticated HTTP surface."""
        try:
            username, password, _ = _desktop_readiness_credentials(settings)
        except DesktopReadinessCredentialError:
            return {"configured": False, "auth_challenge": False, "authenticated": False}
        try:
            async with httpx.AsyncClient(
                timeout=settings.desktop_readiness_timeout_seconds,
                follow_redirects=False,
            ) as client:
                endpoint = settings.desktop_kasm_internal_url.rstrip("/") + "/"
                anonymous = await client.get(endpoint)
                authenticated = await client.get(
                    endpoint,
                    auth=httpx.BasicAuth(username, password),
                )
            return {
                "configured": True,
                "auth_challenge": anonymous.status_code == 401,
                "authenticated": authenticated.status_code == 200,
            }
        except (httpx.HTTPError, TypeError, ValueError):
            return {"configured": True, "auth_challenge": False, "authenticated": False}


def get_desktop_readiness_probe() -> DesktopReadinessProbe:
    return NetworkDesktopReadinessProbe()


DepDesktopReadinessProbe = Annotated[DesktopReadinessProbe, Depends(get_desktop_readiness_probe)]


class DesktopReadyzCache:
    """Кэш результата readiness-пробы на ``desktop_readiness_cache_seconds``.

    Проба выполняет два HTTP-запроса к Kasm рядом с money-критичной
    Vision-сессией, поэтому входящие запросы не должны транслироваться в новые
    подключения 1:1. Лок сериализует конкурентные запросы: пробу выполняет один,
    остальные получают закэшированный результат.
    TTL <= 0 полностью отключает кэш.
    """

    def __init__(self, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._monotonic = monotonic
        self._lock = Lock()
        self._checks: dict[str, bool] | None = None
        self._expires_at = 0.0
        self._credential_revision = ""

    async def get(self, settings: Settings, probe: DesktopReadinessProbe) -> dict[str, bool]:
        ttl = settings.desktop_readiness_cache_seconds
        credential_revision = _desktop_readiness_revision(settings)
        if ttl <= 0:
            return await probe.check(settings)
        async with self._lock:
            if (
                self._checks is None
                or self._credential_revision != credential_revision
                or self._monotonic() >= self._expires_at
            ):
                self._checks = await probe.check(settings)
                self._expires_at = self._monotonic() + ttl
                self._credential_revision = credential_revision
            return dict(self._checks)


_READYZ_CACHE = DesktopReadyzCache()


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


def _clear_desktop_cookie(response: Response) -> None:
    response.delete_cookie(
        DESKTOP_SESSION_COOKIE,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )


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
<p>{safe_message}</p><a href="{_PANEL_DESKTOP_PAGE}">Вернуться в AdPulse</a>
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
    del settings
    token = request.cookies.get(DESKTOP_SESSION_COOKIE)
    session = await load_desktop_session(redis, token)
    if session is None or token is None:
        return None
    request_hostname = (request.url.hostname or "").lower().rstrip(".")
    if request_hostname != session.expected_hostname:
        await delete_desktop_session(redis, token)
        return None
    recipient = await find_recipient_by_telegram_user_id(
        engine, telegram_user_id=session.telegram_user_id
    )
    if recipient is None or recipient.role != "owner":
        await delete_desktop_session(redis, token)
        return None
    return token, session


@router.get("/desktop-auth/redeem", include_in_schema=False)
async def redeem_desktop_ticket(
    request: Request,
    engine: DepEngine,
    redis: DepRedis,
    settings: DepSettings,
    ticket: str = Query(default=""),
) -> Response:
    try:
        grant = await consume_desktop_ticket(redis, ticket)
        request_hostname = (request.url.hostname or "").lower().rstrip(".")
        if request_hostname != grant.expected_hostname:
            raise DesktopAccessError("Desktop ticket выпущен для другого hostname")
        recipient = await find_recipient_by_telegram_user_id(
            engine, telegram_user_id=grant.telegram_user_id
        )
        if recipient is None or recipient.role != "owner":
            raise DesktopAccessError("Telegram-аккаунт больше не является владельцем")
        token, _ = await create_desktop_session(
            redis,
            telegram_user_id=grant.telegram_user_id,
            source=grant.source,
            expected_hostname=grant.expected_hostname,
            presentation=grant.presentation,
            ttl=settings.desktop_access_session_ttl_seconds,
        )
    except DesktopAccessError as exc:
        return _desktop_error(str(exc))
    response = RedirectResponse("/", status_code=303, headers=_NO_STORE)
    _set_desktop_cookie(response, token, settings.desktop_access_session_ttl_seconds)
    return response


@router.get("/desktop-auth/verify", include_in_schema=False)
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
                "Remote-User": DESKTOP_PRINCIPAL,
                "X-Desktop-Transport": "kasm",
                "X-Desktop-Presentation": session.presentation,
            },
        )
    response = RedirectResponse(_PANEL_DESKTOP_PAGE, status_code=303, headers=_NO_STORE)
    _clear_desktop_cookie(response)
    return response


@router.get("/desktop-auth/profile", include_in_schema=False)
async def desktop_session_profile(
    request: Request,
    engine: DepEngine,
    redis: DepRedis,
    settings: DepSettings,
) -> Response:
    """Expose only the authenticated presentation profile to the first-party client."""
    resolved = await _resolve_desktop_session(request, engine, redis, settings)
    if resolved is None:
        response = JSONResponse(
            {"detail": "desktop_session_required"},
            status_code=401,
            headers=_NO_STORE,
        )
        _clear_desktop_cookie(response)
        return response
    _, session = resolved
    return JSONResponse(
        {"presentation": session.presentation},
        status_code=200,
        headers={**_NO_STORE, "X-Content-Type-Options": "nosniff"},
    )


@router.post("/desktop/logout", include_in_schema=False)
async def logout_desktop(request: Request, redis: DepRedis) -> Response:
    token = request.cookies.get(DESKTOP_SESSION_COOKIE)
    await delete_desktop_session(redis, token)
    response = RedirectResponse(_PANEL_DESKTOP_PAGE, status_code=303, headers=_NO_STORE)
    _clear_desktop_cookie(response)
    return response


@router.get("/desktop-kasm-readyz", include_in_schema=False)
@router.get("/desktop-readyz", include_in_schema=False)
async def desktop_readyz(
    settings: DepSettings,
    probe: DepDesktopReadinessProbe,
) -> Response:
    checks = await _READYZ_CACHE.get(settings, probe)
    ready = bool(checks) and all(checks.values())
    return JSONResponse(
        {"status": "ok" if ready else "not_ready", "checks": checks},
        status_code=200 if ready else 503,
        headers=_NO_STORE,
    )


__all__ = ["DesktopReadyzCache", "get_desktop_readiness_probe", "router"]

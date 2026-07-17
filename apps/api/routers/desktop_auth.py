# -*- coding: utf-8 -*-
"""Technical endpoints for the production Vision desktop session gate."""

from __future__ import annotations

import html
import logging
import time
from asyncio import LimitOverrunError, open_connection, wait_for
from typing import Annotated, Protocol
from urllib.parse import quote

import asyncpg
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
    mark_desktop_owner_checked,
)
from core.config import Settings, reveal_secret
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


def _encode_guacamole_instruction(opcode: str, *arguments: str) -> bytes:
    elements = (opcode, *arguments)
    return (",".join(f"{len(value)}.{value}" for value in elements) + ";").encode()


def _decode_guacamole_instruction(raw: bytes) -> tuple[str, list[str]]:
    """Decode one length-prefixed Guacamole instruction, rejecting ambiguity."""
    try:
        document = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("invalid Guacamole instruction encoding") from exc
    if not document.endswith(";"):
        raise ValueError("unterminated Guacamole instruction")
    elements: list[str] = []
    cursor = 0
    end = len(document) - 1
    while cursor < end:
        dot = document.find(".", cursor, end)
        if dot < 0 or not document[cursor:dot].isdigit():
            raise ValueError("invalid Guacamole element length")
        length = int(document[cursor:dot])
        if length > 16_384:
            raise ValueError("Guacamole element is too large")
        value_start = dot + 1
        value_end = value_start + length
        if value_end > end:
            raise ValueError("truncated Guacamole element")
        elements.append(document[value_start:value_end])
        cursor = value_end
        if cursor < end:
            if document[cursor] != ",":
                raise ValueError("invalid Guacamole element separator")
            cursor += 1
    if not elements or not elements[0] or cursor != end:
        raise ValueError("empty Guacamole instruction")
    return elements[0], elements[1:]


class NetworkDesktopReadinessProbe:
    """Fail-closed HTTP, JDBC and real guacd-to-VNC connection checks."""

    async def _guacamole_ready(self, settings: Settings) -> bool:
        token = ""
        try:
            async with httpx.AsyncClient(
                timeout=settings.desktop_readiness_timeout_seconds,
                follow_redirects=False,
            ) as client:
                endpoint = settings.desktop_guacamole_internal_url.rstrip("/")
                response = await client.post(
                    f"{endpoint}/api/tokens",
                    headers={
                        "Remote-User": DESKTOP_PRINCIPAL,
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    content=b"",
                )
                if response.status_code != 200:
                    return False
                payload = response.json()
                token = str(payload.get("authToken") or "")
                authenticated = payload.get("username") == DESKTOP_PRINCIPAL and bool(token)
                if token:
                    await client.delete(f"{endpoint}/api/tokens/{quote(token, safe='')}")
                return authenticated
        except (httpx.HTTPError, TypeError, ValueError):
            return False

    async def _jdbc_ready(self, settings: Settings) -> bool:
        password = reveal_secret(settings.desktop_guacamole_postgres_password)
        if not password:
            return False
        connection: asyncpg.Connection | None = None
        try:
            connection = await wait_for(
                asyncpg.connect(
                    host=settings.desktop_guacamole_postgres_host,
                    port=settings.desktop_guacamole_postgres_port,
                    database=settings.desktop_guacamole_postgres_db,
                    user=settings.desktop_guacamole_postgres_user,
                    password=password,
                ),
                timeout=settings.desktop_readiness_timeout_seconds,
            )
            rows = await wait_for(
                connection.fetch(
                    "SELECT c.connection_id, c.connection_name, c.protocol "
                    "FROM guacamole_connection AS c ORDER BY c.connection_id"
                ),
                timeout=settings.desktop_readiness_timeout_seconds,
            )
            if (
                len(rows) != 1
                or rows[0]["connection_name"] != "Vision Desktop"
                or rows[0]["protocol"] != "vnc"
            ):
                return False
            connection_id = int(rows[0]["connection_id"])
            permissions = await wait_for(
                connection.fetch(
                    "SELECT e.name, p.permission FROM guacamole_connection_permission AS p "
                    "JOIN guacamole_entity AS e ON e.entity_id = p.entity_id "
                    "WHERE p.connection_id = $1",
                    connection_id,
                ),
                timeout=settings.desktop_readiness_timeout_seconds,
            )
            if [dict(row) for row in permissions] != [
                {"name": DESKTOP_PRINCIPAL, "permission": "READ"}
            ]:
                return False
            parameter_rows = await wait_for(
                connection.fetch(
                    "SELECT parameter_name, parameter_value "
                    "FROM guacamole_connection_parameter WHERE connection_id = $1",
                    connection_id,
                ),
                timeout=settings.desktop_readiness_timeout_seconds,
            )
            parameters = {row["parameter_name"]: row["parameter_value"] for row in parameter_rows}
            return parameters == {
                "hostname": "127.0.0.1",
                "port": "5900",
                "password": parameters.get("password"),
                "width": "1366",
                "height": "768",
                "disable-display-resize": "true",
                "read-only": "false",
            } and bool(parameters["password"])
        except (asyncpg.PostgresError, OSError, TimeoutError):
            return False
        finally:
            if connection is not None:
                await connection.close()

    async def _guacd_vnc_ready(self, settings: Settings) -> bool:
        """Complete guacd's VNC handshake and require its ``ready`` response.

        A TCP connect to port 5900 from the API cannot work because TigerVNC is
        intentionally bound to loopback inside webtop's network namespace.
        guacd shares that namespace, so a successful ``ready`` proves the real
        guacd -> 127.0.0.1:5900 path, including VNC authentication.
        """
        password = reveal_secret(settings.desktop_vnc_password)
        try:
            password_bytes = password.encode("ascii")
        except UnicodeEncodeError:
            return False
        if len(password_bytes) != 8 or any(byte < 0x20 or byte > 0x7E for byte in password_bytes):
            return False
        timeout = settings.desktop_readiness_timeout_seconds
        writer = None
        try:
            reader, writer = await wait_for(
                open_connection(settings.desktop_guacd_host, settings.desktop_guacd_port),
                timeout=timeout,
            )
            writer.write(_encode_guacamole_instruction("select", "vnc"))
            await wait_for(writer.drain(), timeout=timeout)

            opcode = ""
            arguments: list[str] = []
            for _ in range(8):
                raw = await wait_for(reader.readuntil(b";"), timeout=timeout)
                opcode, arguments = _decode_guacamole_instruction(raw)
                if opcode == "args":
                    break
                if opcode in {"error", "disconnect"}:
                    return False
            if opcode != "args" or not arguments:
                return False

            values = {
                "hostname": "127.0.0.1",
                "port": "5900",
                "password": password,
                "width": "1366",
                "height": "768",
                "disable-display-resize": "true",
                "read-only": "false",
            }
            connect_values = [
                argument if argument.startswith("VERSION_") else values.get(argument, "")
                for argument in arguments
            ]
            handshake = b"".join(
                (
                    _encode_guacamole_instruction("size", "1366", "768", "96"),
                    _encode_guacamole_instruction("audio"),
                    _encode_guacamole_instruction("video"),
                    _encode_guacamole_instruction("image", "image/png", "image/jpeg"),
                    _encode_guacamole_instruction("timezone", "Europe/Kaliningrad"),
                    _encode_guacamole_instruction("name", "desktop-readiness"),
                    _encode_guacamole_instruction("connect", *connect_values),
                )
            )
            writer.write(handshake)
            await wait_for(writer.drain(), timeout=timeout)

            for _ in range(16):
                raw = await wait_for(reader.readuntil(b";"), timeout=timeout)
                opcode, _ = _decode_guacamole_instruction(raw)
                if opcode == "ready":
                    writer.write(_encode_guacamole_instruction("disconnect"))
                    await wait_for(writer.drain(), timeout=timeout)
                    return True
                if opcode in {"error", "disconnect"}:
                    return False
            return False
        except (OSError, EOFError, LimitOverrunError, TimeoutError, ValueError):
            return False
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass

    async def check(self, settings: Settings) -> dict[str, bool]:
        import asyncio

        guacamole, jdbc, guacd_vnc = await asyncio.gather(
            self._guacamole_ready(settings),
            self._jdbc_ready(settings),
            self._guacd_vnc_ready(settings),
        )
        return {"guacamole": guacamole, "jdbc": jdbc, "guacd_vnc": guacd_vnc}


def get_desktop_readiness_probe() -> DesktopReadinessProbe:
    return NetworkDesktopReadinessProbe()


DepDesktopReadinessProbe = Annotated[DesktopReadinessProbe, Depends(get_desktop_readiness_probe)]


def _set_desktop_cookie(response: Response, token: str, max_age: int) -> None:
    response.set_cookie(
        DESKTOP_SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/desktop",
    )


def _clear_desktop_cookie(response: Response) -> None:
    response.delete_cookie(
        DESKTOP_SESSION_COOKIE,
        path="/desktop",
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
        if session is None:
            return None
    return token, session


@router.get("/desktop-auth/redeem", include_in_schema=False)
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
    response = RedirectResponse("/desktop/", status_code=303, headers=_NO_STORE)
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
        return Response(
            status_code=200,
            headers={**_NO_STORE, "Remote-User": DESKTOP_PRINCIPAL},
        )
    response = RedirectResponse(_PANEL_DESKTOP_PAGE, status_code=303, headers=_NO_STORE)
    _clear_desktop_cookie(response)
    return response


@router.post("/desktop/logout", include_in_schema=False)
async def logout_desktop(request: Request, redis: DepRedis) -> Response:
    await delete_desktop_session(redis, request.cookies.get(DESKTOP_SESSION_COOKIE))
    response = RedirectResponse(_PANEL_DESKTOP_PAGE, status_code=303, headers=_NO_STORE)
    _clear_desktop_cookie(response)
    return response


@router.get("/desktop-readyz", include_in_schema=False)
async def desktop_readyz(
    settings: DepSettings,
    probe: DepDesktopReadinessProbe,
) -> Response:
    checks = await probe.check(settings)
    ready = bool(checks) and all(checks.values())
    return JSONResponse(
        {"status": "ok" if ready else "not_ready", "checks": checks},
        status_code=200 if ready else 503,
        headers=_NO_STORE,
    )


__all__ = ["get_desktop_readiness_probe", "router"]

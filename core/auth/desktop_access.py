# -*- coding: utf-8 -*-
"""One-time grants and server-side sessions for the protected Vision desktop."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from base64 import b64encode
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

DESKTOP_SESSION_COOKIE = "adpulse_desktop_session"

_TICKET_PREFIX = "desktop_auth:ticket:"
_SESSION_PREFIX = "desktop_auth:session:"
_GUACAMOLE_PATH = "/guacamole/"


class DesktopAccessError(ValueError):
    """An expected desktop authentication failure."""


@dataclass(frozen=True)
class DesktopGrant:
    telegram_user_id: int
    source: str
    issued_at: int
    expires_at: int


@dataclass(frozen=True)
class DesktopSession:
    telegram_user_id: int
    source: str
    issued_at: int
    expires_at: int
    owner_checked_at: int


def _digest_key(prefix: str, token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"{prefix}{digest}"


def build_desktop_launch_url(base_url: str, ticket: str) -> str:
    """Build the fixed redeem URL without allowing an arbitrary redirect host."""
    normalized = base_url.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise DesktopAccessError("Некорректный desktop base URL")
    if not ticket:
        raise DesktopAccessError("Desktop ticket отсутствует")
    return f"{normalized}/desktop-auth/redeem?{urlencode({'ticket': ticket})}"


def build_guacamole_auth_data(
    secret_hex: str,
    *,
    telegram_user_id: int,
    vnc_password: str,
    ttl: int,
    now_ms: int | None = None,
) -> str:
    """Create the signed/encrypted payload consumed by Guacamole JSON Auth.

    Guacamole's official extension requires HMAC-SHA256(signature + JSON),
    followed by AES-128-CBC with an all-zero IV and standard base64 encoding.
    """
    try:
        key = bytes.fromhex(secret_hex)
    except ValueError as exc:
        raise DesktopAccessError("Guacamole JSON secret должен быть hex-строкой") from exc
    if len(key) != 16 or len(secret_hex) != 32:
        raise DesktopAccessError("Guacamole JSON secret должен содержать ровно 32 hex-символа")
    if telegram_user_id <= 0 or ttl <= 0:
        raise DesktopAccessError("Нельзя создать Guacamole grant")
    try:
        encoded_password = vnc_password.encode("ascii")
    except UnicodeEncodeError as exc:
        raise DesktopAccessError("VNC password должен состоять из ASCII-символов") from exc
    if len(encoded_password) != 8 or any(byte < 0x20 or byte > 0x7E for byte in encoded_password):
        raise DesktopAccessError(
            "VNC password должен содержать ровно 8 ASCII-символов (только печатных)"
        )

    current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    document = {
        "username": f"telegram:{telegram_user_id}",
        "expires": current_ms + ttl * 1000,
        "connections": {
            "Vision Desktop": {
                "protocol": "vnc",
                "parameters": {
                    "hostname": "127.0.0.1",
                    "port": "5900",
                    "password": vnc_password,
                    # The X11 display is shared with browser-agent. Never let a
                    # phone rotation resize that shared server-side desktop.
                    "disable-display-resize": "true",
                    "read-only": "false",
                },
            }
        },
    }
    plaintext = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    signed = hmac.new(key, plaintext, hashlib.sha256).digest() + plaintext
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(signed) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(bytes(16))).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return b64encode(encrypted).decode("ascii")


def build_guacamole_connect_url(auth_data: str) -> str:
    if not auth_data:
        raise DesktopAccessError("Guacamole auth data отсутствует")
    return f"{_GUACAMOLE_PATH}?{urlencode({'data': auth_data})}"


async def create_desktop_ticket(
    redis: Any,
    *,
    telegram_user_id: int,
    source: str,
    ttl: int,
    now: int | None = None,
) -> tuple[str, DesktopGrant]:
    current = int(time.time()) if now is None else int(now)
    if telegram_user_id <= 0 or ttl <= 0 or not source:
        raise DesktopAccessError("Нельзя создать desktop ticket")
    ticket = secrets.token_urlsafe(48)
    grant = DesktopGrant(
        telegram_user_id=telegram_user_id,
        source=source,
        issued_at=current,
        expires_at=current + ttl,
    )
    created = await redis.set(
        _digest_key(_TICKET_PREFIX, ticket),
        json.dumps(asdict(grant), separators=(",", ":")),
        ex=ttl,
        nx=True,
    )
    if not created:
        raise DesktopAccessError("Не удалось создать desktop ticket")
    return ticket, grant


async def consume_desktop_ticket(
    redis: Any, ticket: str, *, now: int | None = None
) -> DesktopGrant:
    if not ticket:
        raise DesktopAccessError("Desktop ticket отсутствует")
    raw = await redis.getdel(_digest_key(_TICKET_PREFIX, ticket))
    if not raw:
        raise DesktopAccessError("Desktop ticket истёк или уже использован")
    try:
        payload = json.loads(raw)
        grant = DesktopGrant(
            telegram_user_id=int(payload["telegram_user_id"]),
            source=str(payload["source"]),
            issued_at=int(payload["issued_at"]),
            expires_at=int(payload["expires_at"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DesktopAccessError("Повреждён desktop ticket") from exc
    current = int(time.time()) if now is None else int(now)
    if grant.telegram_user_id <= 0 or not grant.source or grant.expires_at <= current:
        raise DesktopAccessError("Desktop ticket истёк или невалиден")
    return grant


async def create_desktop_session(
    redis: Any,
    *,
    telegram_user_id: int,
    source: str,
    ttl: int,
    now: int | None = None,
) -> tuple[str, DesktopSession]:
    current = int(time.time()) if now is None else int(now)
    if telegram_user_id <= 0 or ttl <= 0 or not source:
        raise DesktopAccessError("Нельзя создать desktop-сессию")
    token = secrets.token_urlsafe(48)
    session = DesktopSession(
        telegram_user_id=telegram_user_id,
        source=source,
        issued_at=current,
        expires_at=current + ttl,
        owner_checked_at=current,
    )
    await redis.set(
        _digest_key(_SESSION_PREFIX, token),
        json.dumps(asdict(session), separators=(",", ":")),
        ex=ttl,
    )
    return token, session


async def load_desktop_session(
    redis: Any, token: str | None, *, now: int | None = None
) -> DesktopSession | None:
    if not token:
        return None
    key = _digest_key(_SESSION_PREFIX, token)
    raw = await redis.get(key)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        session = DesktopSession(
            telegram_user_id=int(payload["telegram_user_id"]),
            source=str(payload["source"]),
            issued_at=int(payload["issued_at"]),
            expires_at=int(payload["expires_at"]),
            owner_checked_at=int(payload["owner_checked_at"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        await redis.delete(key)
        return None
    current = int(time.time()) if now is None else int(now)
    if session.telegram_user_id <= 0 or not session.source or session.expires_at <= current:
        await redis.delete(key)
        return None
    return session


async def mark_desktop_owner_checked(
    redis: Any, token: str, session: DesktopSession, now: int
) -> DesktopSession:
    updated = DesktopSession(**{**asdict(session), "owner_checked_at": int(now)})
    remaining = updated.expires_at - int(now)
    if remaining > 0:
        await redis.set(
            _digest_key(_SESSION_PREFIX, token),
            json.dumps(asdict(updated), separators=(",", ":")),
            ex=remaining,
        )
    return updated


async def delete_desktop_session(redis: Any, token: str | None) -> None:
    if token:
        await redis.delete(_digest_key(_SESSION_PREFIX, token))


__all__ = [
    "DESKTOP_SESSION_COOKIE",
    "DesktopAccessError",
    "DesktopGrant",
    "DesktopSession",
    "build_desktop_launch_url",
    "build_guacamole_auth_data",
    "build_guacamole_connect_url",
    "consume_desktop_ticket",
    "create_desktop_session",
    "create_desktop_ticket",
    "delete_desktop_session",
    "load_desktop_session",
    "mark_desktop_owner_checked",
]

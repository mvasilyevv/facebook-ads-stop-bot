# -*- coding: utf-8 -*-
"""One-time tickets and revocable sessions for the protected Vision desktop."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit

DesktopPresentation = Literal["desktop", "mobile"]

DESKTOP_SESSION_COOKIE = "__Secure-adpulse_desktop_session_v5"
DESKTOP_PRINCIPAL = "adpulse-desktop"

_TICKET_PREFIX = "desktop_access:v5:ticket:"
_SESSION_PREFIX = "desktop_access:v5:session:"


class DesktopAccessError(ValueError):
    """An expected desktop authentication failure."""


@dataclass(frozen=True)
class DesktopGrant:
    telegram_user_id: int
    source: str
    issued_at: int
    expires_at: int
    expected_hostname: str
    presentation: DesktopPresentation


@dataclass(frozen=True)
class DesktopSession:
    telegram_user_id: int
    source: str
    issued_at: int
    expires_at: int
    expected_hostname: str
    presentation: DesktopPresentation


def _presentation(value: str) -> DesktopPresentation:
    if value not in {"desktop", "mobile"}:
        raise DesktopAccessError("Некорректный desktop presentation profile")
    return value


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


async def create_desktop_ticket(
    redis: Any,
    *,
    telegram_user_id: int,
    source: str,
    expected_hostname: str,
    presentation: DesktopPresentation,
    ttl: int,
    now: int | None = None,
) -> tuple[str, DesktopGrant]:
    current = int(time.time()) if now is None else int(now)
    hostname = expected_hostname.strip().lower().rstrip(".")
    resolved_presentation = _presentation(presentation)
    if telegram_user_id <= 0 or ttl <= 0 or not source or not hostname:
        raise DesktopAccessError("Нельзя создать desktop ticket")
    ticket = secrets.token_urlsafe(48)
    grant = DesktopGrant(
        telegram_user_id=telegram_user_id,
        source=source,
        issued_at=current,
        expires_at=current + ttl,
        expected_hostname=hostname,
        presentation=resolved_presentation,
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
            expected_hostname=str(payload["expected_hostname"]).strip().lower().rstrip("."),
            presentation=_presentation(str(payload["presentation"])),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DesktopAccessError("Повреждён desktop ticket") from exc
    current = int(time.time()) if now is None else int(now)
    if (
        grant.telegram_user_id <= 0
        or not grant.source
        or grant.expires_at <= current
        or not grant.expected_hostname
    ):
        raise DesktopAccessError("Desktop ticket истёк или невалиден")
    return grant


async def create_desktop_session(
    redis: Any,
    *,
    telegram_user_id: int,
    source: str,
    expected_hostname: str,
    presentation: DesktopPresentation,
    ttl: int,
    now: int | None = None,
) -> tuple[str, DesktopSession]:
    current = int(time.time()) if now is None else int(now)
    hostname = expected_hostname.strip().lower().rstrip(".")
    resolved_presentation = _presentation(presentation)
    if telegram_user_id <= 0 or ttl <= 0 or not source or not hostname:
        raise DesktopAccessError("Нельзя создать desktop-сессию")
    token = secrets.token_urlsafe(48)
    session = DesktopSession(
        telegram_user_id=telegram_user_id,
        source=source,
        issued_at=current,
        expires_at=current + ttl,
        expected_hostname=hostname,
        presentation=resolved_presentation,
    )
    created = await redis.set(
        _digest_key(_SESSION_PREFIX, token),
        json.dumps(asdict(session), separators=(",", ":")),
        ex=ttl,
        nx=True,
    )
    if not created:
        raise DesktopAccessError("Не удалось создать desktop-сессию")
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
            expected_hostname=str(payload["expected_hostname"]).strip().lower().rstrip("."),
            presentation=_presentation(str(payload["presentation"])),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        await redis.delete(key)
        return None
    current = int(time.time()) if now is None else int(now)
    if (
        session.telegram_user_id <= 0
        or not session.source
        or session.expires_at <= current
        or not session.expected_hostname
    ):
        await redis.delete(key)
        return None
    return session


async def delete_desktop_session(redis: Any, token: str | None) -> None:
    if token:
        await redis.delete(_digest_key(_SESSION_PREFIX, token))


__all__ = [
    "DESKTOP_SESSION_COOKIE",
    "DESKTOP_PRINCIPAL",
    "DesktopAccessError",
    "DesktopGrant",
    "DesktopPresentation",
    "DesktopSession",
    "build_desktop_launch_url",
    "consume_desktop_ticket",
    "create_desktop_session",
    "create_desktop_ticket",
    "delete_desktop_session",
    "load_desktop_session",
]

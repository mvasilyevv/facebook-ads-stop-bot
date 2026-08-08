# -*- coding: utf-8 -*-
"""Security primitives for owner-only Telegram OIDC panel authentication."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode, urlsplit

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

TELEGRAM_ISSUER = "https://oauth.telegram.org"
TELEGRAM_AUTH_URL = f"{TELEGRAM_ISSUER}/auth"
TELEGRAM_TOKEN_URL = f"{TELEGRAM_ISSUER}/token"
TELEGRAM_JWKS_URL = f"{TELEGRAM_ISSUER}/.well-known/jwks.json"
PANEL_SESSION_COOKIE = "__Secure-adpulse_panel_session_v1"

_MAX_OPAQUE_TOKEN_LENGTH = 256


class PanelAuthError(ValueError):
    """An expected, user-safe authentication failure."""


class TelegramSigningKeyNotFound(PanelAuthError):
    """The token references a key absent from the current JWKS snapshot."""


@dataclass(frozen=True)
class OidcAttempt:
    nonce: str
    code_verifier: str
    return_to: str


@dataclass(frozen=True)
class PanelTicket:
    telegram_user_id: int
    source: str
    return_to: str
    issued_at: int
    expires_at: int


@dataclass(frozen=True)
class PanelSession:
    telegram_user_id: int
    role: str
    source: str
    issued_at: int
    expires_at: int


def safe_return_to(value: str | None) -> str:
    """Allow only same-origin relative application paths."""
    candidate = (value or "").strip()
    if not candidate.startswith("/") or candidate.startswith("//") or len(candidate) > 2048:
        return "/"
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return "/"
    if parsed.scheme or parsed.netloc or "\\" in candidate or parsed.path.startswith("/auth/"):
        return "/"
    return candidate


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except Exception as exc:  # noqa: BLE001 - normalized to a safe auth error
        raise PanelAuthError("Некорректный Telegram ID token") from exc


def _digest_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def _valid_opaque_token(token: str | None) -> bool:
    return bool(token) and len(token or "") <= _MAX_OPAQUE_TOKEN_LENGTH


def _at_epoch(epoch: int | None = None) -> datetime:
    if epoch is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc)


def _epoch(value: datetime) -> int:
    return int(value.timestamp())


def create_oidc_authorization(
    *, client_id: str, redirect_uri: str, return_to: str | None
) -> tuple[str, str, OidcAttempt]:
    """Create state, nonce and an S256 PKCE challenge for Telegram OIDC."""
    if not client_id.strip() or not redirect_uri.strip():
        raise PanelAuthError("Telegram Login пока не настроен")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    nonce = secrets.token_urlsafe(32)
    challenge = _b64url_encode(hashlib.sha256(verifier.encode("ascii")).digest())
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    attempt = OidcAttempt(
        nonce=nonce,
        code_verifier=verifier,
        return_to=safe_return_to(return_to),
    )
    return state, f"{TELEGRAM_AUTH_URL}?{urlencode(params)}", attempt


async def save_oidc_attempt(
    engine: AsyncEngine,
    state: str,
    attempt: OidcAttempt,
    ttl: int,
    *,
    now: int | None = None,
) -> None:
    if not _valid_opaque_token(state) or ttl <= 0:
        raise PanelAuthError("Некорректный Telegram Login state")
    current = _at_epoch(now)
    async with engine.begin() as conn:
        created = await conn.execute(
            text(
                """
                INSERT INTO panel_oidc_attempts
                    (state_digest, nonce, code_verifier, return_to, created_at, expires_at)
                VALUES
                    (:digest, :nonce, :code_verifier, :return_to,
                     :created_at, :expires_at)
                ON CONFLICT (state_digest) DO NOTHING
                """
            ),
            {
                "digest": _digest_token(state),
                "nonce": attempt.nonce,
                "code_verifier": attempt.code_verifier,
                "return_to": safe_return_to(attempt.return_to),
                "created_at": current,
                "expires_at": datetime.fromtimestamp(
                    int(current.timestamp()) + ttl, tz=timezone.utc
                ),
            },
        )
    if (created.rowcount or 0) != 1:
        raise PanelAuthError("Не удалось создать Telegram Login")


async def consume_oidc_attempt(
    engine: AsyncEngine, state: str, *, now: int | None = None
) -> OidcAttempt:
    if not _valid_opaque_token(state):
        raise PanelAuthError("Telegram Login state отсутствует")
    async with engine.begin() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        """
                    DELETE FROM panel_oidc_attempts
                    WHERE state_digest = :digest
                      AND expires_at > :now
                    RETURNING nonce, code_verifier, return_to
                    """
                    ),
                    {"digest": _digest_token(state), "now": _at_epoch(now)},
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise PanelAuthError("Telegram Login истёк или уже был использован")
    return OidcAttempt(
        nonce=str(row["nonce"]),
        code_verifier=str(row["code_verifier"]),
        return_to=safe_return_to(row["return_to"]),
    )


def _select_rsa_key(jwks: dict[str, Any], kid: str) -> rsa.RSAPublicKey:
    keys = jwks.get("keys")
    if not isinstance(keys, list):
        raise PanelAuthError("Telegram JWKS не содержит ключей")
    for item in keys:
        if not isinstance(item, dict) or item.get("kid") != kid:
            continue
        if item.get("kty") != "RSA" or not item.get("n") or not item.get("e"):
            raise PanelAuthError("Telegram JWKS содержит неподдерживаемый ключ")
        try:
            modulus = int.from_bytes(_b64url_decode(str(item["n"])), "big")
            exponent = int.from_bytes(_b64url_decode(str(item["e"])), "big")
            return rsa.RSAPublicNumbers(exponent, modulus).public_key()
        except (TypeError, ValueError) as exc:
            raise PanelAuthError("Telegram JWKS содержит некорректный RSA-ключ") from exc
    raise TelegramSigningKeyNotFound("Ключ подписи Telegram не найден")


def verify_telegram_id_token(
    token: str,
    *,
    jwks: dict[str, Any],
    client_id: str,
    nonce: str,
    now: int | None = None,
    clock_skew_seconds: int = 60,
) -> dict[str, Any]:
    """Verify RS256 signature and all security-relevant Telegram OIDC claims."""
    try:
        header_part, payload_part, signature_part = token.split(".")
        header = json.loads(_b64url_decode(header_part))
        claims = json.loads(_b64url_decode(payload_part))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise PanelAuthError("Некорректный Telegram ID token") from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise PanelAuthError("Некорректный Telegram ID token")
    if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
        raise PanelAuthError("Неподдерживаемая подпись Telegram ID token")

    key = _select_rsa_key(jwks, header["kid"])
    try:
        key.verify(
            _b64url_decode(signature_part),
            f"{header_part}.{payload_part}".encode("ascii"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception as exc:  # noqa: BLE001 - normalized to a safe auth error
        raise PanelAuthError("Подпись Telegram ID token не прошла проверку") from exc

    current = int(time.time()) if now is None else int(now)
    if claims.get("iss") != TELEGRAM_ISSUER:
        raise PanelAuthError("Некорректный издатель Telegram ID token")
    audience = claims.get("aud")
    if audience != client_id and not (isinstance(audience, list) and client_id in audience):
        raise PanelAuthError("Telegram ID token выпущен для другого приложения")
    if isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != client_id:
        raise PanelAuthError("Telegram ID token содержит некорректный authorized party")
    try:
        expires_at = int(claims["exp"])
        issued_at = int(claims["iat"])
        not_before = int(claims.get("nbf", issued_at))
        telegram_user_id = int(claims["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PanelAuthError("Telegram ID token не содержит обязательных полей") from exc
    if expires_at < current - clock_skew_seconds:
        raise PanelAuthError("Telegram ID token истёк")
    if issued_at > current + clock_skew_seconds or not_before > current + clock_skew_seconds:
        raise PanelAuthError("Telegram ID token ещё не действует")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise PanelAuthError("Telegram Login nonce не совпал")
    if telegram_user_id <= 0:
        raise PanelAuthError("Некорректный Telegram user ID")
    claims["id"] = telegram_user_id
    return claims


async def create_panel_ticket(
    engine: AsyncEngine,
    *,
    telegram_user_id: int,
    source: str,
    return_to: str | None,
    ttl: int,
    now: int | None = None,
) -> tuple[str, PanelTicket]:
    current = int(time.time()) if now is None else int(now)
    if telegram_user_id <= 0 or not source or len(source) > 32 or ttl <= 0:
        raise PanelAuthError("Нельзя создать login ticket")
    ticket = secrets.token_urlsafe(48)
    grant = PanelTicket(
        telegram_user_id=telegram_user_id,
        source=source,
        return_to=safe_return_to(return_to),
        issued_at=current,
        expires_at=current + ttl,
    )
    async with engine.begin() as conn:
        created = await conn.execute(
            text(
                """
                INSERT INTO panel_login_tickets
                    (ticket_digest, telegram_user_id, source, return_to,
                     issued_at, expires_at)
                VALUES
                    (:digest, :telegram_user_id, :source, :return_to,
                     :issued_at, :expires_at)
                ON CONFLICT (ticket_digest) DO NOTHING
                """
            ),
            {
                "digest": _digest_token(ticket),
                "telegram_user_id": grant.telegram_user_id,
                "source": grant.source,
                "return_to": grant.return_to,
                "issued_at": _at_epoch(grant.issued_at),
                "expires_at": _at_epoch(grant.expires_at),
            },
        )
    if (created.rowcount or 0) != 1:
        raise PanelAuthError("Не удалось создать login ticket")
    return ticket, grant


async def consume_panel_ticket(
    engine: AsyncEngine, ticket: str, *, now: int | None = None
) -> PanelTicket:
    if not _valid_opaque_token(ticket):
        raise PanelAuthError("Login ticket отсутствует")
    async with engine.begin() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        """
                    DELETE FROM panel_login_tickets
                    WHERE ticket_digest = :digest
                      AND expires_at > :now
                    RETURNING telegram_user_id, source, return_to, issued_at, expires_at
                    """
                    ),
                    {"digest": _digest_token(ticket), "now": _at_epoch(now)},
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise PanelAuthError("Login ticket истёк или уже был использован")
    return PanelTicket(
        telegram_user_id=int(row["telegram_user_id"]),
        source=str(row["source"]),
        return_to=safe_return_to(row["return_to"]),
        issued_at=_epoch(row["issued_at"]),
        expires_at=_epoch(row["expires_at"]),
    )


async def create_panel_session(
    engine: AsyncEngine,
    *,
    telegram_user_id: int,
    role: str,
    source: str,
    ttl: int,
    now: int | None = None,
) -> tuple[str, PanelSession]:
    current = int(time.time()) if now is None else int(now)
    if telegram_user_id <= 0 or role != "owner" or not source or len(source) > 32 or ttl <= 0:
        raise PanelAuthError("Нельзя создать owner-сессию")
    token = secrets.token_urlsafe(48)
    session = PanelSession(
        telegram_user_id=telegram_user_id,
        role=role,
        source=source,
        issued_at=current,
        expires_at=current + ttl,
    )
    async with engine.begin() as conn:
        created = await conn.execute(
            text(
                """
                INSERT INTO panel_sessions
                    (token_digest, telegram_user_id, role, source,
                     issued_at, expires_at)
                VALUES
                    (:digest, :telegram_user_id, :role, :source,
                     :issued_at, :expires_at)
                ON CONFLICT (token_digest) DO NOTHING
                """
            ),
            {
                "digest": _digest_token(token),
                "telegram_user_id": session.telegram_user_id,
                "role": session.role,
                "source": session.source,
                "issued_at": _at_epoch(session.issued_at),
                "expires_at": _at_epoch(session.expires_at),
            },
        )
    if (created.rowcount or 0) != 1:
        raise PanelAuthError("Не удалось создать owner-сессию")
    return token, session


async def load_panel_session(
    engine: AsyncEngine, token: str | None, *, now: int | None = None
) -> PanelSession | None:
    if not _valid_opaque_token(token):
        return None
    current = _at_epoch(now)
    async with engine.begin() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT session.telegram_user_id, session.role, session.source,
                           session.issued_at, session.expires_at
                    FROM panel_sessions AS session
                    JOIN telegram_recipients AS recipient
                      ON recipient.telegram_user_id = session.telegram_user_id
                     AND recipient.role = 'owner'
                     AND recipient.revoked_at IS NULL
                    WHERE session.token_digest = :digest
                      AND session.expires_at > :now
                      AND session.role = 'owner'
                      AND session.telegram_user_id > 0
                    """
                    ),
                    {"digest": _digest_token(token or ""), "now": current},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            await conn.execute(
                text(
                    """
                    DELETE FROM panel_sessions AS session
                    WHERE session.token_digest = :digest
                      AND (
                          session.expires_at <= :now
                          OR NOT EXISTS (
                              SELECT 1
                              FROM telegram_recipients AS recipient
                              WHERE recipient.telegram_user_id = session.telegram_user_id
                                AND recipient.role = 'owner'
                                AND recipient.revoked_at IS NULL
                          )
                      )
                    """
                ),
                {"digest": _digest_token(token or ""), "now": current},
            )
    if row is None:
        return None
    return PanelSession(
        telegram_user_id=int(row["telegram_user_id"]),
        role=str(row["role"]),
        source=str(row["source"]),
        issued_at=_epoch(row["issued_at"]),
        expires_at=_epoch(row["expires_at"]),
    )


async def delete_panel_session(engine: AsyncEngine, token: str | None) -> None:
    if not _valid_opaque_token(token):
        return
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM panel_sessions WHERE token_digest = :digest"),
            {"digest": _digest_token(token or "")},
        )


async def cleanup_expired_panel_auth_records(
    engine: AsyncEngine,
    *,
    batch_size: int = 1000,
    now: int | None = None,
) -> dict[str, int]:
    """Bound expiry cleanup so auth traffic never performs an unbounded delete."""
    if batch_size <= 0 or batch_size > 10_000:
        raise ValueError("batch_size must be between 1 and 10000")
    current = _at_epoch(now)
    deleted: dict[str, int] = {}
    for table_name in ("panel_oidc_attempts", "panel_login_tickets", "panel_sessions"):
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    f"""
                    WITH expired AS (
                        SELECT ctid
                        FROM {table_name}
                        WHERE expires_at <= :now
                        ORDER BY expires_at
                        LIMIT :batch_size
                        FOR UPDATE SKIP LOCKED
                    )
                    DELETE FROM {table_name} AS record
                    USING expired
                    WHERE record.ctid = expired.ctid
                    """
                ),
                {"now": current, "batch_size": batch_size},
            )
        deleted[table_name] = result.rowcount or 0
    return deleted


__all__ = [
    "PANEL_SESSION_COOKIE",
    "TELEGRAM_JWKS_URL",
    "TELEGRAM_TOKEN_URL",
    "OidcAttempt",
    "PanelAuthError",
    "PanelSession",
    "PanelTicket",
    "TelegramSigningKeyNotFound",
    "cleanup_expired_panel_auth_records",
    "consume_oidc_attempt",
    "consume_panel_ticket",
    "create_oidc_authorization",
    "create_panel_session",
    "create_panel_ticket",
    "delete_panel_session",
    "load_panel_session",
    "safe_return_to",
    "save_oidc_attempt",
    "verify_telegram_id_token",
]

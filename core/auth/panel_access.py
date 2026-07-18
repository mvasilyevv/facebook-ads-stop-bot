# -*- coding: utf-8 -*-
"""Security primitives for owner-only Telegram OIDC panel authentication."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

TELEGRAM_ISSUER = "https://oauth.telegram.org"
TELEGRAM_AUTH_URL = f"{TELEGRAM_ISSUER}/auth"
TELEGRAM_TOKEN_URL = f"{TELEGRAM_ISSUER}/token"
TELEGRAM_JWKS_URL = f"{TELEGRAM_ISSUER}/.well-known/jwks.json"
PANEL_SESSION_COOKIE = "__Secure-adpulse_panel_session_v1"

_STATE_PREFIX = "panel_auth:v1:oidc_state:"
_TICKET_PREFIX = "panel_auth:v1:ticket:"
_SESSION_PREFIX = "panel_auth:v1:session:"


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
    owner_checked_at: int


def safe_return_to(value: str | None) -> str:
    """Allow only same-origin relative application paths."""
    candidate = (value or "").strip()
    if not candidate.startswith("/") or candidate.startswith("//"):
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


def _digest_key(prefix: str, token: str) -> str:
    return f"{prefix}{hashlib.sha256(token.encode('utf-8')).hexdigest()}"


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


async def save_oidc_attempt(redis: Any, state: str, attempt: OidcAttempt, ttl: int) -> None:
    if not state or ttl <= 0:
        raise PanelAuthError("Некорректный Telegram Login state")
    created = await redis.set(
        f"{_STATE_PREFIX}{state}",
        json.dumps(asdict(attempt), separators=(",", ":")),
        ex=ttl,
        nx=True,
    )
    if not created:
        raise PanelAuthError("Не удалось создать Telegram Login")


async def consume_oidc_attempt(redis: Any, state: str) -> OidcAttempt:
    if not state:
        raise PanelAuthError("Telegram Login state отсутствует")
    raw = await redis.getdel(f"{_STATE_PREFIX}{state}")
    if not raw:
        raise PanelAuthError("Telegram Login истёк или уже был использован")
    try:
        payload = json.loads(raw)
        return OidcAttempt(
            nonce=str(payload["nonce"]),
            code_verifier=str(payload["code_verifier"]),
            return_to=safe_return_to(payload.get("return_to")),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PanelAuthError("Повреждён Telegram Login state") from exc


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
    redis: Any,
    *,
    telegram_user_id: int,
    source: str,
    return_to: str | None,
    ttl: int,
    now: int | None = None,
) -> tuple[str, PanelTicket]:
    current = int(time.time()) if now is None else int(now)
    if telegram_user_id <= 0 or not source or ttl <= 0:
        raise PanelAuthError("Нельзя создать login ticket")
    ticket = secrets.token_urlsafe(48)
    grant = PanelTicket(
        telegram_user_id=telegram_user_id,
        source=source,
        return_to=safe_return_to(return_to),
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
        raise PanelAuthError("Не удалось создать login ticket")
    return ticket, grant


async def consume_panel_ticket(redis: Any, ticket: str, *, now: int | None = None) -> PanelTicket:
    if not ticket:
        raise PanelAuthError("Login ticket отсутствует")
    raw = await redis.getdel(_digest_key(_TICKET_PREFIX, ticket))
    if not raw:
        raise PanelAuthError("Login ticket истёк или уже был использован")
    try:
        payload = json.loads(raw)
        grant = PanelTicket(
            telegram_user_id=int(payload["telegram_user_id"]),
            source=str(payload["source"]),
            return_to=safe_return_to(payload.get("return_to")),
            issued_at=int(payload["issued_at"]),
            expires_at=int(payload["expires_at"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PanelAuthError("Повреждён login ticket") from exc
    current = int(time.time()) if now is None else int(now)
    if grant.telegram_user_id <= 0 or not grant.source or grant.expires_at <= current:
        raise PanelAuthError("Login ticket истёк или невалиден")
    return grant


async def create_panel_session(
    redis: Any,
    *,
    telegram_user_id: int,
    role: str,
    source: str,
    ttl: int,
    now: int | None = None,
) -> tuple[str, PanelSession]:
    current = int(time.time()) if now is None else int(now)
    if telegram_user_id <= 0 or role != "owner" or not source or ttl <= 0:
        raise PanelAuthError("Нельзя создать owner-сессию")
    token = secrets.token_urlsafe(48)
    session = PanelSession(
        telegram_user_id=telegram_user_id,
        role=role,
        source=source,
        issued_at=current,
        expires_at=current + ttl,
        owner_checked_at=current,
    )
    created = await redis.set(
        _digest_key(_SESSION_PREFIX, token),
        json.dumps(asdict(session), separators=(",", ":")),
        ex=ttl,
        nx=True,
    )
    if not created:
        raise PanelAuthError("Не удалось создать owner-сессию")
    return token, session


async def load_panel_session(
    redis: Any, token: str | None, *, now: int | None = None
) -> PanelSession | None:
    if not token:
        return None
    key = _digest_key(_SESSION_PREFIX, token)
    raw = await redis.get(key)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        session = PanelSession(
            telegram_user_id=int(payload["telegram_user_id"]),
            role=str(payload["role"]),
            source=str(payload["source"]),
            issued_at=int(payload["issued_at"]),
            expires_at=int(payload["expires_at"]),
            owner_checked_at=int(payload["owner_checked_at"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        await redis.delete(key)
        return None
    current = int(time.time()) if now is None else int(now)
    if session.role != "owner" or session.telegram_user_id <= 0 or session.expires_at <= current:
        await redis.delete(key)
        return None
    return session


async def mark_owner_checked(
    redis: Any, token: str, session: PanelSession, now: int
) -> PanelSession | None:
    """Refresh a role check without resurrecting a concurrently logged-out session."""
    updated = PanelSession(**{**asdict(session), "owner_checked_at": int(now)})
    remaining = updated.expires_at - int(now)
    if remaining <= 0:
        return None
    refreshed = await redis.set(
        _digest_key(_SESSION_PREFIX, token),
        json.dumps(asdict(updated), separators=(",", ":")),
        ex=remaining,
        xx=True,
    )
    return updated if refreshed else None


async def delete_panel_session(redis: Any, token: str | None) -> None:
    if token:
        await redis.delete(_digest_key(_SESSION_PREFIX, token))


__all__ = [
    "PANEL_SESSION_COOKIE",
    "TELEGRAM_JWKS_URL",
    "TELEGRAM_TOKEN_URL",
    "OidcAttempt",
    "PanelAuthError",
    "PanelSession",
    "PanelTicket",
    "TelegramSigningKeyNotFound",
    "consume_oidc_attempt",
    "consume_panel_ticket",
    "create_oidc_authorization",
    "create_panel_session",
    "create_panel_ticket",
    "delete_panel_session",
    "load_panel_session",
    "mark_owner_checked",
    "safe_return_to",
    "save_oidc_attempt",
    "verify_telegram_id_token",
]

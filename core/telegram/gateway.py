# -*- coding: utf-8 -*-
"""HTML-only Telegram Bot API gateway for the durable notification plane.

The gateway deliberately has no Rich Messages and no hidden retry/sleep. Retry
decisions belong to the PostgreSQL delivery worker, where ``retry_after`` is
persisted and survives process restarts. Exceptions never retain the httpx
request object or the token-bearing URL.
"""

from __future__ import annotations

import hashlib
import logging
import re
from enum import StrEnum
from typing import Any

import httpx
from pydantic import SecretStr

from core.config import get_settings

_MESSAGE_LIMIT = 4096
_SECRET_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
_ACTION_CAPABILITY_RE = re.compile(r"(?<![A-Za-z0-9_-])a:[A-Za-z0-9_-]{22}(?![A-Za-z0-9_-])")
_NAVIGATION_CAPABILITY_RE = re.compile(
    r"(?i)(?P<prefix>nav(?:=|%3D))[A-Za-z0-9_-]{22}(?![A-Za-z0-9_-])"
)


def _redact_capability_patterns(value: object) -> str:
    description = str(value or "")
    description = _ACTION_CAPABILITY_RE.sub("a:<redacted>", description)
    return _NAVIGATION_CAPABILITY_RE.sub(
        lambda match: f"{match.group('prefix')}<redacted>",
        description,
    )


# httpx INFO records include the full token-bearing request URL.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def telegram_credential_fingerprint(bot_token: str | SecretStr) -> str:
    """Return the stable one-way credential identity used by DB fencing."""
    raw = (
        bot_token.get_secret_value() if isinstance(bot_token, SecretStr) else str(bot_token)
    ).strip()
    if not raw:
        raise ValueError("Telegram bot token is not configured")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class TelegramFailureKind(StrEnum):
    RATE_LIMITED = "rate_limited"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"
    TRANSIENT = "transient"
    UNKNOWN = "unknown"


class TelegramGatewayError(RuntimeError):
    """Sanitized failure returned to the durable delivery policy."""

    def __init__(
        self,
        *,
        method: str,
        kind: TelegramFailureKind,
        error_code: int | None = None,
        retry_after: float | None = None,
        description: str = "",
    ) -> None:
        self.method = method
        self.kind = kind
        self.error_code = error_code
        self.retry_after = retry_after
        self.description = _redact_capability_patterns(description)[:500]
        code = f" code={error_code}" if error_code is not None else ""
        super().__init__(f"Telegram {method} failed: {kind.value}{code}")


def _failure_kind(status_code: int, error_code: int | None) -> TelegramFailureKind:
    code = error_code or status_code
    if code == 429:
        return TelegramFailureKind.RATE_LIMITED
    if code == 401:
        return TelegramFailureKind.UNAUTHORIZED
    if code == 403:
        return TelegramFailureKind.FORBIDDEN
    if code == 404:
        return TelegramFailureKind.NOT_FOUND
    if code >= 500:
        return TelegramFailureKind.TRANSIENT
    if code >= 400:
        return TelegramFailureKind.INVALID_REQUEST
    return TelegramFailureKind.UNKNOWN


def _retry_after(response: httpx.Response, data: dict[str, Any]) -> float | None:
    raw: Any = response.headers.get("Retry-After")
    if raw is None:
        raw = (data.get("parameters") or {}).get("retry_after")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return max(0.0, value)


class TelegramHTMLGateway:
    """Minimal target gateway: send/edit/ack via official HTML Bot API."""

    def __init__(
        self,
        bot_token: str | SecretStr,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        raw = (
            bot_token.get_secret_value() if isinstance(bot_token, SecretStr) else str(bot_token)
        ).strip()
        if not raw:
            raise ValueError("Telegram bot token is not configured")
        self._bot_token = SecretStr(raw)
        self._credential_fingerprint = telegram_credential_fingerprint(raw)
        self._bot_api_origin = get_settings().telegram_bot_api_origin
        self._http = http_client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_http_client = http_client is None
        self._timeout_seconds = timeout_seconds

    def __repr__(self) -> str:
        return "TelegramHTMLGateway(token=<redacted>)"

    @property
    def credential_fingerprint(self) -> str:
        """One-way identifier used only to detect an explicit token rotation."""
        return self._credential_fingerprint

    def _method_url(self, method: str) -> str:
        token = self._bot_token.get_secret_value()
        return f"{self._bot_api_origin}/bot{token}/{method}"

    def _sanitize_description(
        self,
        value: object,
        *,
        payload: dict[str, Any] | None = None,
    ) -> str:
        description = str(value or "")
        token = self._bot_token.get_secret_value()
        if token:
            description = description.replace(token, "<redacted>")
        sensitive_values: set[str] = set()

        def collect(item: object, *, key: str | None = None) -> None:
            if isinstance(item, dict):
                for nested_key, nested_value in item.items():
                    collect(nested_value, key=str(nested_key))
                return
            if isinstance(item, (list, tuple)):
                for nested_value in item:
                    collect(nested_value, key=key)
                return
            if not isinstance(item, str):
                return
            if key in {"callback_data", "callback_query_id", "secret_token"}:
                sensitive_values.add(item)
            if _NAVIGATION_CAPABILITY_RE.search(item):
                sensitive_values.add(item)

        if payload is not None:
            collect(payload)
        for sensitive in sorted(sensitive_values, key=len, reverse=True):
            if sensitive:
                description = description.replace(sensitive, "<redacted>")
        return _redact_capability_patterns(description)[:500]

    async def _post(self, method: str, payload: dict[str, Any]) -> Any:
        try:
            response = await self._http.post(
                self._method_url(method),
                json=payload,
                timeout=self._timeout_seconds,
            )
        except httpx.HTTPError:
            # A send may have reached Telegram even when its response is lost.
            # Retrying it would create a duplicate, so only idempotent Bot API
            # methods are classified as transient here.
            raise TelegramGatewayError(
                method=method,
                kind=(
                    TelegramFailureKind.UNKNOWN
                    if method == "sendMessage"
                    else TelegramFailureKind.TRANSIENT
                ),
            ) from None

        try:
            raw_data = response.json()
        except ValueError:
            kind = (
                TelegramFailureKind.UNKNOWN
                if method == "sendMessage"
                else TelegramFailureKind.TRANSIENT
            )
            raise TelegramGatewayError(
                method=method,
                kind=kind,
                error_code=response.status_code,
            ) from None
        if not isinstance(raw_data, dict):
            raise TelegramGatewayError(
                method=method,
                kind=(
                    TelegramFailureKind.UNKNOWN
                    if method == "sendMessage"
                    else TelegramFailureKind.TRANSIENT
                ),
            )

        data: dict[str, Any] = raw_data
        error_code_raw = data.get("error_code")
        try:
            error_code = int(error_code_raw) if error_code_raw is not None else None
        except (TypeError, ValueError):
            error_code = None
        if response.is_error or data.get("ok") is not True:
            kind = _failure_kind(response.status_code, error_code)
            if method == "sendMessage" and kind is TelegramFailureKind.TRANSIENT:
                kind = TelegramFailureKind.UNKNOWN
            raise TelegramGatewayError(
                method=method,
                kind=kind,
                error_code=error_code or response.status_code,
                retry_after=_retry_after(response, data),
                description=self._sanitize_description(
                    data.get("description"),
                    payload=payload,
                ),
            )
        return data.get("result")

    async def send_message(
        self,
        *,
        chat_id: int | str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = "HTML",
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        if parse_mode not in (None, "HTML"):
            raise ValueError("The target Telegram gateway supports HTML only")
        if not text or len(text) > _MESSAGE_LIMIT:
            raise ValueError("Telegram message text must contain 1..4096 characters")
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if reply_to_message_id is not None:
            payload["reply_parameters"] = {
                "message_id": reply_to_message_id,
                "allow_sending_without_reply": False,
            }
        result = await self._post("sendMessage", payload)
        if not isinstance(result, dict):
            raise TelegramGatewayError(method="sendMessage", kind=TelegramFailureKind.UNKNOWN)
        raw_message_id = result.get("message_id")
        try:
            message_id = int(raw_message_id) if raw_message_id is not None else None
        except (TypeError, ValueError):
            message_id = None
        if message_id is None or message_id <= 0:
            # Telegram may have accepted the request, so auto-resend would risk a
            # duplicate. The delivery worker persists this as UNKNOWN.
            raise TelegramGatewayError(method="sendMessage", kind=TelegramFailureKind.UNKNOWN)
        return dict(result)

    async def edit_message(
        self,
        *,
        chat_id: int | str,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        if message_id <= 0:
            raise ValueError("message_id must be positive")
        if not text or len(text) > _MESSAGE_LIMIT:
            raise ValueError("Telegram message text must contain 1..4096 characters")
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        await self._post("editMessageText", payload)

    async def answer_callback_query(self, callback_query_id: str, text: str = "") -> None:
        if not callback_query_id:
            raise ValueError("callback_query_id is required")
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text[:200]
        await self._post("answerCallbackQuery", payload)

    async def edit_message_reply_markup(
        self,
        *,
        chat_id: int | str,
        message_id: int,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        await self._post("editMessageReplyMarkup", payload)

    async def send_chat_action(self, *, chat_id: int | str, action: str = "typing") -> None:
        await self._post("sendChatAction", {"chat_id": chat_id, "action": action})

    async def get_chat(self, *, chat_id: int | str) -> dict[str, Any]:
        result = await self._post("getChat", {"chat_id": chat_id})
        if not isinstance(result, dict):
            raise TelegramGatewayError(method="getChat", kind=TelegramFailureKind.UNKNOWN)
        return dict(result)

    async def get_me(self) -> dict[str, Any]:
        result = await self._post("getMe", {})
        if not isinstance(result, dict):
            raise TelegramGatewayError(method="getMe", kind=TelegramFailureKind.UNKNOWN)
        return dict(result)

    async def set_chat_menu_button(
        self,
        *,
        web_app_url: str,
        button_text: str = "📱 Открыть",
        chat_id: int | None = None,
    ) -> None:
        """Set the Mini App menu button through the single sanitized gateway."""
        if not web_app_url.startswith("https://"):
            raise ValueError("web_app_url must be HTTPS")
        payload: dict[str, Any] = {
            "menu_button": {
                "type": "web_app",
                "text": button_text,
                "web_app": {"url": web_app_url},
            }
        }
        if chat_id is not None:
            payload["chat_id"] = int(chat_id)
        await self._post("setChatMenuButton", payload)

    async def set_webhook(
        self,
        *,
        url: str,
        secret_token: str | SecretStr,
        drop_pending_updates: bool = False,
    ) -> None:
        if not url.startswith("https://"):
            raise ValueError("Telegram webhook URL must use HTTPS")
        secret = (
            secret_token.get_secret_value()
            if isinstance(secret_token, SecretStr)
            else str(secret_token)
        )
        if not _SECRET_TOKEN_RE.fullmatch(secret):
            raise ValueError("Telegram webhook secret has an invalid format")
        await self._post(
            "setWebhook",
            {
                "url": url,
                "secret_token": secret,
                "allowed_updates": ["message", "edited_message", "callback_query"],
                "drop_pending_updates": drop_pending_updates,
            },
        )

    async def get_webhook_info(self) -> dict[str, Any]:
        """Return Telegram's current webhook state for idempotent cutover checks."""
        result = await self._post("getWebhookInfo", {})
        if not isinstance(result, dict):
            raise TelegramGatewayError(method="getWebhookInfo", kind=TelegramFailureKind.UNKNOWN)
        return dict(result)

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> None:
        """Remove the remote webhook through Telegram's idempotent API."""
        await self._post(
            "deleteWebhook",
            {"drop_pending_updates": bool(drop_pending_updates)},
        )

    async def close(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()


__all__ = [
    "TelegramFailureKind",
    "TelegramGatewayError",
    "TelegramHTMLGateway",
    "telegram_credential_fingerprint",
]

# -*- coding: utf-8 -*-
"""Pure helpers for Telegram settings runtime fields.

The configured token is read from a detached snapshot.  Public bot identity is
resolved from Telegram directly; Redis is neither truth nor a compatibility
cache for the notification plane.
"""

from __future__ import annotations

import logging
import secrets

from sqlalchemy.ext.asyncio import AsyncEngine

from core.safe_diagnostics import safe_exception_diagnostic

logger = logging.getLogger(__name__)


def compute_is_authorized(config: object | None) -> bool:
    """Return whether the stored bot credential is currently enabled.

    Args:
        config: ORM-объект TelegramConfig или None.
    """
    if config is None:
        return False
    if getattr(config, "is_enabled", True) is not True:
        return False
    token = getattr(config, "bot_token_encrypted", None)
    return bool(token)


async def compute_bot_username(
    config: object | None,
    *,
    engine: AsyncEngine,
) -> str | None:
    """Resolve the configured bot username from Telegram ``getMe``.

    ``None`` is an explicit unknown value when no token is configured, the
    encrypted token cannot be opened, or Telegram cannot confirm the identity.
    """
    if not compute_is_authorized(config):
        return None

    try:
        bot_generation = int(getattr(config, "webhook_generation", 0) or 0)
        fingerprint_value = getattr(config, "credential_fingerprint", None)
        credential_fingerprint = str(fingerprint_value or "")
        if bot_generation <= 0 or len(credential_fingerprint) != 64:
            return None
        # Validate the detached public digest before constructing a gateway.
        from core.telegram.outbound_authority import credential_fingerprint_bytes

        credential_fingerprint_bytes(credential_fingerprint)
    except (TypeError, ValueError):
        return None

    # Расшифровываем токен и запрашиваем /getMe.
    try:
        from core.crypto import decrypt

        enc = getattr(config, "bot_token_encrypted", None)
        if not enc:
            return None
        token = decrypt(enc)
        if not token:
            return None
    except Exception as exc:
        # Decrypt/provider exceptions are not trusted log payloads: they can
        # accidentally retain credential material supplied by an adapter.
        logger.warning(
            "Не удалось расшифровать bot_token_encrypted (error_type=%s)",
            type(exc).__name__,
        )
        return None

    gateway = None
    try:
        from core.telegram.gateway import TelegramHTMLGateway
        from core.telegram.outbound_authority import hold_telegram_outbound_authority

        gateway = TelegramHTMLGateway(token, timeout_seconds=5.0)
        if not secrets.compare_digest(
            gateway.credential_fingerprint,
            credential_fingerprint,
        ):
            logger.warning("Telegram settings credential fingerprint mismatch")
            return None
        async with hold_telegram_outbound_authority(
            engine,
            bot_generation=bot_generation,
            credential_fingerprint=credential_fingerprint,
        ) as authorized:
            if not authorized:
                return None
            identity = await gateway.get_me()
        username_value = identity.get("username")
        return str(username_value) if username_value else None
    except Exception as exc:
        from core.telegram.gateway import TelegramFailureKind, TelegramGatewayError

        if isinstance(exc, TelegramGatewayError) and exc.kind is TelegramFailureKind.UNAUTHORIZED:
            from core.telegram.notifications import (
                open_telegram_auth_incident_in_transaction,
            )
            from core.telegram.outbound_authority import (
                telegram_failure_authority_is_current,
            )

            async with engine.begin() as conn:
                await open_telegram_auth_incident_in_transaction(
                    conn,
                    error_code="telegram_unauthorized",
                    credential_fingerprint=credential_fingerprint,
                    source="settings_get_me",
                )
                if not await telegram_failure_authority_is_current(
                    conn,
                    bot_generation=bot_generation,
                    credential_fingerprint=credential_fingerprint,
                ):
                    await conn.rollback()
        # httpx exceptions retain the full request URL, which contains the bot
        # token. Log only the type and never the exception representation.
        logger.warning(
            "Ошибка запроса getMe к Telegram API (error_type=%s)",
            type(exc).__name__,
        )
        return None
    finally:
        if gateway is not None:
            try:
                await gateway.close()
            except Exception as exc:
                logger.debug(
                    "Не удалось закрыть Telegram gateway (%s)",
                    safe_exception_diagnostic(exc),
                )


def compute_auth_deep_link(
    bot_username: str | None,
    invite_code: str | None,
) -> str | None:
    """Возвращает безопасную ссылку на бота без invite-кода в URL.

    Одноразовый invite-код возвращается отдельно как ``activation_command``.
    Так он не остаётся в browser history, Referer и tracker URL.

    Args:
        bot_username: username бота без @, или None.
        invite_code: активный invite-код; используется только как presence guard.
    """
    username = (bot_username or "").strip().removeprefix("@")
    code = (invite_code or "").strip()
    if not username or not code:
        return None
    return f"https://t.me/{username}"


def compute_activation_command(invite_code: str | None) -> str | None:
    """Возвращает команду активации с тем же invite-кодом, что и deep-link.

    Команда, которую нужно отправить боту для регистрации получателя.
    """
    code = (invite_code or "").strip()
    if not code:
        return None
    return f"/start {code}"

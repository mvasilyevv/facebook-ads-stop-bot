# -*- coding: utf-8 -*-
"""Async access to Telegram configuration, recipients and invites."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.config import Settings, get_settings, reveal_secret
from core.crypto import decrypt, encrypt
from core.telegram.owner_roster import lock_owner_roster

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelegramConfig:
    """Snapshot telegram_config из БД."""

    bot_token: str  # уже расшифрован
    webhook_ready: bool
    webhook_generation: int
    updated_at: datetime


@dataclass(frozen=True)
class _StoredTelegramConfig:
    """Сырые поля singleton-строки до расшифровки токена."""

    bot_token_encrypted: str
    is_enabled: bool
    webhook_ready: bool
    webhook_generation: int
    updated_at: datetime


async def _select_telegram_config(conn: AsyncConnection) -> _StoredTelegramConfig | None:
    """Читает singleton через уже открытое соединение."""
    row = (
        await conn.execute(
            text(
                """
                SELECT bot_token_encrypted, is_enabled,
                       (
                           webhook_state = 'configured'
                           AND webhook_operation = 'configure'
                           AND webhook_applied_generation = webhook_generation
                       ) AS webhook_ready,
                       webhook_generation, updated_at
                FROM telegram_config
                WHERE singleton_key = 'default'
                """
            )
        )
    ).first()
    if row is None:
        return None
    return _StoredTelegramConfig(
        bot_token_encrypted=str(row[0] or ""),
        is_enabled=bool(row[1]),
        webhook_ready=bool(row[2]),
        webhook_generation=int(row[3]),
        updated_at=row[4],
    )


async def bootstrap_telegram_config_from_env(
    engine: AsyncEngine,
    *,
    settings: Settings | None = None,
) -> bool:
    """Однократно импортирует ``TELEGRAM_BOT_TOKEN`` в отсутствующий singleton.

    Это явная release/bootstrap-команда, а не runtime fallback. Существующая
    строка, включая пустой tombstone после явного DELETE в UI, всегда
    авторитетна. ``ON CONFLICT DO NOTHING`` делает повторный и параллельный
    запуск безопасным.

    Returns:
        ``True`` только если текущий вызов создал singleton.
    """

    async with engine.connect() as conn:
        exists = await conn.scalar(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM telegram_config
                    WHERE singleton_key = 'default'
                )
                """
            )
        )
    if exists:
        return False

    resolved_settings = settings or get_settings()
    token = reveal_secret(resolved_settings.telegram_bot_token).strip()
    if not token:
        return False

    try:
        encrypted = encrypt(token)
    except Exception as exc:
        # Не включаем ни plaintext/ciphertext, ни exception message в logger:
        # сторонняя реализация crypto теоретически может вложить вход в текст ошибки.
        logger.error(
            "Не удалось зашифровать TELEGRAM_BOT_TOKEN для telegram_config (error_type=%s)",
            type(exc).__name__,
        )
        raise RuntimeError(
            f"Telegram token bootstrap encryption failed (error_type={type(exc).__name__})"
        ) from None

    from core.telegram.gateway import telegram_credential_fingerprint
    from core.telegram.webhook_configuration import (
        bind_webhook_generation,
        resolve_webhook_target,
    )

    try:
        target = resolve_webhook_target(
            frontend_origin=resolved_settings.frontend_origin,
            secret_token=resolved_settings.telegram_webhook_secret,
        )
    except ValueError:
        target = None

    async with engine.begin() as conn:
        inserted = (
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_config
                        (singleton_key, bot_token_encrypted,
                         bot_token_fingerprint, is_enabled,
                         webhook_generation, webhook_operation,
                         webhook_desired_url, webhook_secret_digest,
                         webhook_state, webhook_scheduled_at)
                    VALUES
                        ('default', :bot_token_encrypted,
                         :bot_token_fingerprint, TRUE,
                         :webhook_generation, :webhook_operation,
                         :webhook_desired_url, :webhook_secret_digest,
                         CAST(:webhook_state AS VARCHAR(16)),
                         CASE
                             WHEN CAST(:webhook_state AS VARCHAR(16)) = 'pending'
                             THEN NOW()
                             ELSE NULL
                         END)
                    ON CONFLICT (singleton_key) DO NOTHING
                    RETURNING singleton_key
                    """
                ),
                {
                    "bot_token_encrypted": encrypted,
                    "bot_token_fingerprint": bytes.fromhex(telegram_credential_fingerprint(token)),
                    "webhook_generation": 1 if target is not None else 0,
                    "webhook_operation": ("configure" if target is not None else None),
                    "webhook_desired_url": (
                        bind_webhook_generation(target.url, 1) if target is not None else None
                    ),
                    "webhook_secret_digest": (target.secret_digest if target is not None else None),
                    "webhook_state": "pending" if target is not None else "unconfigured",
                },
            )
        ).first()

    if inserted is not None:
        logger.info(
            "telegram_config создан из bootstrap environment (webhook_target_configured=%s)",
            target is not None,
        )
    return inserted is not None


async def load_telegram_config(engine: AsyncEngine) -> TelegramConfig | None:
    """Читает singleton telegram_config + расшифровывает токен.

    Runtime-источник — только PostgreSQL. Отсутствующая строка и tombstone после
    явного отключения одинаково fail closed; окружение здесь не читается.
    Возвращает None, если DB-конфигурация отсутствует, отключена или невалидна.
    """
    async with engine.connect() as conn:
        stored = await _select_telegram_config(conn)

    if stored is None:
        return None

    enc = stored.bot_token_encrypted
    if not stored.is_enabled or not enc:
        return None

    try:
        token = decrypt(enc).strip()
    except Exception as exc:
        logger.error(
            "Не смог расшифровать bot_token_encrypted (error_type=%s)",
            type(exc).__name__,
        )
        return None

    if not token:
        return None

    return TelegramConfig(
        bot_token=token,
        webhook_ready=stored.webhook_ready,
        webhook_generation=stored.webhook_generation,
        updated_at=stored.updated_at,
    )


async def telegram_generation_is_authoritative(
    engine: AsyncEngine,
    *,
    bot_generation: int,
) -> bool:
    """Return whether a TMA/inbox generation is the enabled DB authority."""
    if bot_generation <= 0:
        return False
    async with engine.connect() as conn:
        return bool(
            await conn.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM telegram_config
                        WHERE singleton_key = 'default'
                          AND is_enabled
                          AND bot_token_encrypted <> ''
                          AND webhook_operation = 'configure'
                          AND webhook_state = 'configured'
                          AND webhook_applied_generation = webhook_generation
                          AND webhook_generation = :bot_generation
                    )
                    """
                ),
                {"bot_generation": int(bot_generation)},
            )
        )


@dataclass(frozen=True)
class Recipient:
    """Запись получателя — кто имеет доступ к боту."""

    chat_id: int
    telegram_user_id: int
    username: str | None
    role: str  # owner / recipient

    def is_owner(self) -> bool:
        """True если роль владельца — гейт для money-действий (ACL)."""
        return self.role == "owner"


async def find_recipient(
    engine: AsyncEngine,
    *,
    chat_id: int,
    telegram_user_id: int,
) -> Recipient | None:
    """Поиск активного (не revoked) recipient'а.

    Возвращает None если такого пользователя нет в списке доступа.
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT chat_id, telegram_user_id, username, role
                    FROM telegram_recipients
                    WHERE chat_id = :cid AND telegram_user_id = :uid
                      AND revoked_at IS NULL
                    LIMIT 1
                    """
                ),
                {"cid": int(chat_id), "uid": int(telegram_user_id)},
            )
        ).first()
    if not row:
        return None
    return Recipient(
        chat_id=int(row[0]),
        telegram_user_id=int(row[1]),
        username=row[2],
        role=str(row[3]),
    )


async def find_recipient_by_telegram_user_id(
    engine: AsyncEngine,
    *,
    telegram_user_id: int,
) -> Recipient | None:
    """Поиск активного recipient'а по telegram_user_id (без chat_id).

    Для TMA-auth: initData содержит user.id, но не chat_id. Берём первого активного
    (не revoked) recipient'а с этим user_id; при нескольких записях приоритет у
    role='owner' (выше привилегия). None — пользователя нет в списке доступа.
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT chat_id, telegram_user_id, username, role
                    FROM telegram_recipients
                    WHERE telegram_user_id = :uid AND revoked_at IS NULL
                    ORDER BY (role = 'owner') DESC
                    LIMIT 1
                    """
                ),
                {"uid": int(telegram_user_id)},
            )
        ).first()
    if not row:
        return None
    return Recipient(
        chat_id=int(row[0]),
        telegram_user_id=int(row[1]),
        username=row[2],
        role=str(row[3]),
    )


async def load_owner_recipients(engine: AsyncEngine) -> list[Recipient]:
    """Все активные owner-recipient'ы (role='owner', не revoked) — адресаты DM-нотификаций.

    Возвращает список (может быть пустым). chat_id — private chat из /start.
    """
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT chat_id, telegram_user_id, username, role
                    FROM telegram_recipients
                    WHERE role = 'owner' AND revoked_at IS NULL
                    ORDER BY chat_id
                    """
                )
            )
        ).all()
    return [Recipient(chat_id=r[0], telegram_user_id=r[1], username=r[2], role=r[3]) for r in rows]


async def load_active_recipients(engine: AsyncEngine) -> list[Recipient]:
    """Все активные recipients (owner + recipient, не revoked) — адресаты DM-рассылки."""
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT chat_id, telegram_user_id, username, role
                    FROM telegram_recipients
                    WHERE revoked_at IS NULL
                    ORDER BY chat_id
                    """
                )
            )
        ).all()
    return [Recipient(chat_id=r[0], telegram_user_id=r[1], username=r[2], role=r[3]) for r in rows]


async def find_active_invite(engine: AsyncEngine, code: str) -> dict | None:
    """Поиск активного (не использованного и не отозванного) invite-кода."""
    if not code:
        return None
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT id, code, role, expires_at
                    FROM telegram_invites
                    WHERE code = :code
                      AND used_at IS NULL
                      AND revoked_at IS NULL
                      AND expires_at > NOW()
                    LIMIT 1
                    """
                ),
                {"code": code},
            )
        ).first()
    if not row:
        return None
    return {"id": row[0], "code": row[1], "role": row[2], "expires_at": row[3]}


async def consume_invite_and_create_recipient(
    engine: AsyncEngine,
    *,
    code: str,
    chat_id: int,
    telegram_user_id: int,
    username: str | None,
    display_name: str | None,
) -> Recipient | None:
    """Atomically consume an active invite and create exactly one recipient.

    The role is read only from the row returned by the guarded UPDATE. Two
    concurrent `/start` requests for one code therefore cannot both mint an
    owner recipient.
    """
    if not code:
        return None
    async with engine.begin() as conn:
        await lock_owner_roster(conn)
        invite = (
            await conn.execute(
                text(
                    """
                    UPDATE telegram_invites
                    SET used_at = clock_timestamp(), used_by = :used_by
                    WHERE code = :code
                      AND used_at IS NULL
                      AND revoked_at IS NULL
                      AND expires_at > clock_timestamp()
                    RETURNING id, role
                    """
                ),
                {"code": code, "used_by": str(telegram_user_id)},
            )
        ).first()
        if invite is None:
            # The recipient transaction may have committed while the webhook
            # worker crashed before finalizing its inbox/reply transaction.
            # Reprocessing that same Telegram update must reproduce success,
            # not turn the already-consumed invite into an error.
            existing = (
                await conn.execute(
                    text(
                        """
                        SELECT r.role, r.username
                        FROM telegram_invites i
                        JOIN telegram_recipients r ON r.invite_id = i.id
                        WHERE i.code = :code
                          AND i.used_at IS NOT NULL
                          AND i.used_by = :used_by
                          AND r.chat_id = :chat_id
                          AND r.telegram_user_id = :telegram_user_id
                          AND r.revoked_at IS NULL
                        LIMIT 1
                        """
                    ),
                    {
                        "code": code,
                        "used_by": str(telegram_user_id),
                        "chat_id": int(chat_id),
                        "telegram_user_id": int(telegram_user_id),
                    },
                )
            ).first()
            if existing is None:
                return None
            return Recipient(
                chat_id=int(chat_id),
                telegram_user_id=int(telegram_user_id),
                username=existing.username,
                role=str(existing.role),
            )
        invite_id = invite.id
        role = str(invite.role)

        # upsert recipient
        recipient_row = (
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_recipients
                        (chat_id, telegram_user_id, username, display_name, role, invite_id)
                    VALUES
                        (:cid, :uid, :un, :dn, :role, :iid)
                    ON CONFLICT (chat_id, telegram_user_id) DO UPDATE
                    SET username = EXCLUDED.username,
                        display_name = EXCLUDED.display_name,
                        role = CASE
                            WHEN telegram_recipients.role = 'owner'
                                 AND telegram_recipients.revoked_at IS NULL
                            THEN 'owner'
                            ELSE EXCLUDED.role
                        END,
                        invite_id = EXCLUDED.invite_id,
                        revoked_at = NULL
                    RETURNING role, username
                    """
                ),
                {
                    "cid": int(chat_id),
                    "uid": int(telegram_user_id),
                    "un": username,
                    "dn": display_name,
                    "role": role,
                    "iid": invite_id,
                },
            )
        ).one()
    return Recipient(
        chat_id=int(chat_id),
        telegram_user_id=int(telegram_user_id),
        username=recipient_row.username,
        role=str(recipient_row.role),
    )


def is_now_aware(dt: datetime | None) -> bool:
    """Проверка что datetime aware (есть tzinfo). Используется для тестов."""
    return dt is not None and dt.tzinfo is not None and dt.tzinfo.utcoffset(dt) is not None


__all__ = [
    "Recipient",
    "TelegramConfig",
    "bootstrap_telegram_config_from_env",
    "consume_invite_and_create_recipient",
    "find_active_invite",
    "find_recipient",
    "find_recipient_by_telegram_user_id",
    "is_now_aware",
    "load_active_recipients",
    "load_owner_recipients",
    "load_telegram_config",
    "telegram_generation_is_authoritative",
]

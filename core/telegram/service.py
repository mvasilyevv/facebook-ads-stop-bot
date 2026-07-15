# -*- coding: utf-8 -*-
"""Async-обвязка для telegram_config / telegram_recipients / telegram_invites.

Минимальный набор функций для poller'а и базовых handlers. Расширяется по мере
миграции других telegram-фич.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.config import get_settings, reveal_secret
from core.crypto import decrypt, encrypt

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelegramConfig:
    """Snapshot telegram_config из БД."""

    bot_token: str  # уже расшифрован
    chat_id: int | None
    poller_offset: int
    poller_heartbeat_at: datetime | None


@dataclass(frozen=True)
class _StoredTelegramConfig:
    """Сырые поля singleton-строки до расшифровки токена."""

    bot_token_encrypted: str
    chat_id: int | None
    poller_offset: int
    poller_heartbeat_at: datetime | None


async def _select_telegram_config(conn: AsyncConnection) -> _StoredTelegramConfig | None:
    """Читает singleton через уже открытое соединение."""
    row = (
        await conn.execute(
            text(
                """
                SELECT bot_token_encrypted, chat_id,
                       poller_offset, poller_heartbeat_at
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
        chat_id=row[1],
        poller_offset=int(row[2] or 0),
        poller_heartbeat_at=row[3],
    )


async def _bootstrap_telegram_config_from_env(
    engine: AsyncEngine,
) -> _StoredTelegramConfig | None:
    """Однократно создаёт отсутствующий singleton из ``TELEGRAM_BOT_TOKEN``.

    Существующая строка, включая пустую после явного DELETE в UI, всегда
    авторитетна. ``ON CONFLICT DO NOTHING`` делает одновременный старт нескольких
    воркеров безопасным: победившая запись затем перечитывается из БД.
    """
    token = reveal_secret(get_settings().telegram_bot_token).strip()
    if not token:
        return None

    try:
        encrypted = encrypt(token)
    except Exception as exc:
        # Не включаем ни plaintext/ciphertext, ни exception message в logger:
        # сторонняя реализация crypto теоретически может вложить вход в текст ошибки.
        logger.error(
            "Не удалось зашифровать TELEGRAM_BOT_TOKEN для telegram_config (error_type=%s)",
            type(exc).__name__,
        )
        return None

    async with engine.begin() as conn:
        inserted = (
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_config (singleton_key, bot_token_encrypted)
                    VALUES ('default', :bot_token_encrypted)
                    ON CONFLICT (singleton_key) DO NOTHING
                    RETURNING singleton_key
                    """
                ),
                {"bot_token_encrypted": encrypted},
            )
        ).first()
        stored = await _select_telegram_config(conn)

    if inserted is not None:
        logger.info(
            "telegram_config создан из TELEGRAM_BOT_TOKEN; дальнейшие настройки из БД "
            "имеют приоритет"
        )
    return stored


async def load_telegram_config(engine: AsyncEngine) -> TelegramConfig | None:
    """Читает singleton telegram_config + расшифровывает токен.

    На чистой БД один раз создаёт отсутствующую строку из ``TELEGRAM_BOT_TOKEN``.
    Существующая строка из UI (даже пустая после явного отключения) приоритетна.
    Возвращает None, если ни БД, ни env не настроены или токен пустой/невалидный.
    """
    async with engine.connect() as conn:
        stored = await _select_telegram_config(conn)

    if stored is None:
        stored = await _bootstrap_telegram_config_from_env(engine)
        if stored is None:
            return None

    enc = stored.bot_token_encrypted
    if not enc:
        return None

    try:
        token = decrypt(enc)
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
        chat_id=stored.chat_id,
        poller_offset=stored.poller_offset,
        poller_heartbeat_at=stored.poller_heartbeat_at,
    )


async def load_poller_offset(engine: AsyncEngine) -> int:
    """Текущий long-polling offset."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT poller_offset FROM telegram_config WHERE singleton_key = 'default'")
            )
        ).first()
    return int(row[0]) if row and row[0] is not None else 0


async def save_poller_offset(engine: AsyncEngine, offset: int) -> None:
    """Сохранить offset после обработки batch'а апдейтов."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE telegram_config
                SET poller_offset = :off, updated_at = NOW()
                WHERE singleton_key = 'default'
                """
            ),
            {"off": int(offset)},
        )


async def touch_poller_heartbeat(engine: AsyncEngine) -> None:
    """Heartbeat poller'а — отметить что он живой."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE telegram_config
                SET poller_heartbeat_at = NOW(), updated_at = NOW()
                WHERE singleton_key = 'default'
                """
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
    invite_id,
    chat_id: int,
    telegram_user_id: int,
    username: str | None,
    display_name: str | None,
    role: str = "recipient",
) -> Recipient:
    """Помечает invite как использованный + создаёт recipient'а в одной транзакции."""
    async with engine.begin() as conn:
        # mark invite as used
        await conn.execute(
            text(
                """
                UPDATE telegram_invites
                SET used_at = NOW(), used_by = :used_by
                WHERE id = :iid
                """
            ),
            {
                "iid": invite_id,
                "used_by": f"{telegram_user_id}",
            },
        )
        # upsert recipient
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
                    role = EXCLUDED.role,
                    invite_id = EXCLUDED.invite_id,
                    revoked_at = NULL
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
    return Recipient(
        chat_id=int(chat_id),
        telegram_user_id=int(telegram_user_id),
        username=username,
        role=role,
    )


def is_now_aware(dt: datetime | None) -> bool:
    """Проверка что datetime aware (есть tzinfo). Используется для тестов."""
    return dt is not None and dt.tzinfo is not None and dt.tzinfo.utcoffset(dt) is not None


__all__ = [
    "Recipient",
    "TelegramConfig",
    "consume_invite_and_create_recipient",
    "find_active_invite",
    "find_recipient",
    "find_recipient_by_telegram_user_id",
    "is_now_aware",
    "load_active_recipients",
    "load_owner_recipients",
    "load_poller_offset",
    "load_telegram_config",
    "save_poller_offset",
    "touch_poller_heartbeat",
]

# -*- coding: utf-8 -*-
"""Общие сервисные функции Telegram-контура."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select, update

from core.config import get_settings
from core.crypto import decrypt
from core.db import get_session_factory
from core.domain import TelegramNotificationStream, TelegramUserRole
from core.models import TelegramInvite, TelegramRecipient, TelegramSettings

INVITE_TTL_HOURS = 24
POLLER_HEARTBEAT_STALE_SECONDS = 45
logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class TelegramDestination:
    """Описание получателя Telegram-уведомлений."""

    chat_id: str
    telegram_user_id: str
    role: str
    username: str
    first_name: str
    is_primary: bool = False
    thread_id_warning: int | None = None
    thread_id_stop: int | None = None
    thread_id_enable: int | None = None
    thread_id_ops: int | None = None
    thread_id_general: int | None = None

    def thread_id_for_stream(self, stream_kind: TelegramNotificationStream) -> int | None:
        """Возвращает thread_id топика для указанного стрима или None."""
        if stream_kind == TelegramNotificationStream.WARNING:
            return self.thread_id_warning
        if stream_kind == TelegramNotificationStream.STOP:
            return self.thread_id_stop
        if stream_kind == TelegramNotificationStream.ENABLE:
            return self.thread_id_enable
        if stream_kind == TelegramNotificationStream.OPS:
            return self.thread_id_ops
        return None


@dataclass(slots=True, frozen=True)
class TelegramAccessContext:
    """Контекст доступа Telegram-пользователя."""

    chat_id: str
    telegram_user_id: str
    role: str
    username: str
    first_name: str
    is_primary: bool = False


def build_start_command(code: str) -> str:
    """Строит команду авторизации для Telegram."""
    return f"/start {code}".strip()


def build_telegram_deep_link(bot_username: str, code: str) -> str:
    """Строит deep link на запуск бота с кодом."""
    if not bot_username or not code:
        return ""
    return f"https://t.me/{bot_username}?start={code}"


def generate_telegram_code(*, length: int = 8) -> str:
    """Генерирует короткий одноразовый код без неоднозначных символов."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def mask_chat_id(chat_id: str) -> str:
    """Маскирует chat_id для UI."""
    value = (chat_id or "").strip()
    if len(value) <= 6:
        return value
    return f"{value[:3]}***{value[-2:]}"


def is_owner_role(role: str) -> bool:
    """Проверяет, что роль пользователя — владелец."""
    return role == TelegramUserRole.OWNER.value


def is_private_chat(chat_type: str | None) -> bool:
    """Проверяет, что бот работает в личном чате."""
    return (chat_type or "").lower() == "private"


def is_supergroup_chat(chat_type: str | None) -> bool:
    """Проверяет, что апдейт пришёл из supergroup."""
    return (chat_type or "").lower() == "supergroup"


async def get_or_create_telegram_settings(session) -> TelegramSettings:
    """Возвращает singleton telegram_settings, создавая строку при необходимости."""
    row = await session.scalar(
        select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
    )
    if row is None:
        row = TelegramSettings(singleton_key="default")
        session.add(row)
        await session.flush()
    return row


def poller_status_from_settings(settings_row: TelegramSettings | None) -> str:
    """Возвращает агрегированный статус poller-а для UI."""
    if settings_row is None or settings_row.poller_heartbeat_at is None:
        return "OFFLINE"

    age_seconds = (datetime.now(UTC) - settings_row.poller_heartbeat_at).total_seconds()
    if age_seconds > POLLER_HEARTBEAT_STALE_SECONDS:
        return "OFFLINE"
    if not settings_row.bot_token_encrypted:
        return "WAITING_BOT_TOKEN"
    if not settings_row.is_authorized:
        return "WAITING_AUTHORIZATION"
    return "ONLINE"


def _has_db_runtime_override(settings_row: TelegramSettings | None) -> bool:
    """Проверяет, что в БД действительно есть Telegram-конфиг, а не пустая строка-заглушка."""
    if settings_row is None:
        return False
    return bool(
        settings_row.bot_token_encrypted
        or settings_row.chat_id
        or settings_row.auth_code
        or settings_row.bot_username
        or settings_row.owner_telegram_user_id
    )


def _destination_from_settings(settings_row: TelegramSettings) -> TelegramDestination | None:
    """Собирает primary destination из telegram_settings."""
    if not settings_row.is_authorized or not settings_row.chat_id:
        return None
    return TelegramDestination(
        chat_id=settings_row.chat_id,
        telegram_user_id=settings_row.owner_telegram_user_id or "",
        role=TelegramUserRole.OWNER.value,
        username=settings_row.owner_username or "",
        first_name=settings_row.owner_first_name or "",
        is_primary=True,
        thread_id_warning=settings_row.thread_id_warning,
        thread_id_stop=settings_row.thread_id_stop,
        thread_id_enable=settings_row.thread_id_enable,
        thread_id_ops=settings_row.thread_id_ops,
        thread_id_general=settings_row.thread_id_general,
    )


async def load_telegram_runtime_config(
    *,
    fallback_token: str = "",
    fallback_chat_id: str = "",
) -> tuple[str, list[TelegramDestination]]:
    """Загружает токен и активных получателей для runtime-уведомлений."""
    factory = get_session_factory()
    try:
        async with factory() as session:
            settings_row = await session.scalar(
                select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
            )

            destinations: list[TelegramDestination] = []
            token = ""

            if settings_row and settings_row.bot_token_encrypted:
                token = decrypt(settings_row.bot_token_encrypted)

            if settings_row and settings_row.is_authorized and token and settings_row.chat_id:
                primary_destination = _destination_from_settings(settings_row)
                if primary_destination is not None:
                    destinations.append(primary_destination)
                recipients = await session.execute(
                    select(TelegramRecipient).where(TelegramRecipient.is_active.is_(True))
                )
                for recipient in recipients.scalars().all():
                    destinations.append(
                        TelegramDestination(
                            chat_id=recipient.chat_id,
                            telegram_user_id=recipient.telegram_user_id or "",
                            role=recipient.role or TelegramUserRole.RECIPIENT.value,
                            username=recipient.username or "",
                            first_name=recipient.first_name or "",
                            is_primary=False,
                        )
                    )
            elif (
                settings_row
                and not settings_row.is_authorized
                and _has_db_runtime_override(settings_row)
            ):
                return "", []
            elif fallback_token and fallback_chat_id:
                token = fallback_token
                destinations.append(
                    TelegramDestination(
                        chat_id=fallback_chat_id,
                        telegram_user_id="",
                        role=TelegramUserRole.OWNER.value,
                        username="",
                        first_name="",
                        is_primary=True,
                    )
                )

            unique_by_chat: dict[str, TelegramDestination] = {}
            for destination in destinations:
                if destination.chat_id and destination.chat_id not in unique_by_chat:
                    unique_by_chat[destination.chat_id] = destination
            return token, list(unique_by_chat.values())
    except Exception:
        logger.error("Не удалось загрузить Telegram runtime config из БД", exc_info=True)

    if fallback_token and fallback_chat_id:
        return fallback_token, [
            TelegramDestination(
                chat_id=fallback_chat_id,
                telegram_user_id="",
                role=TelegramUserRole.OWNER.value,
                username="",
                first_name="",
                is_primary=True,
            )
        ]
    return "", []


async def resolve_telegram_access(
    *,
    chat_id: str,
    telegram_user_id: str,
    chat_type: str | None,
) -> TelegramAccessContext | None:
    """Возвращает контекст доступа пользователя в Telegram."""
    factory = get_session_factory()
    async with factory() as session:
        settings_row = await session.scalar(
            select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
        )
        if settings_row is None:
            return None

        if not is_private_chat(chat_type) and not is_supergroup_chat(chat_type):
            return None

        if (
            settings_row.is_authorized
            and settings_row.chat_id == chat_id
            and (
                not settings_row.owner_telegram_user_id
                or settings_row.owner_telegram_user_id == telegram_user_id
            )
        ):
            return TelegramAccessContext(
                chat_id=chat_id,
                telegram_user_id=telegram_user_id,
                role=TelegramUserRole.OWNER.value,
                username=settings_row.owner_username or "",
                first_name=settings_row.owner_first_name or "",
                is_primary=True,
            )

        recipient_filters = [
            TelegramRecipient.chat_id == chat_id,
            TelegramRecipient.is_active.is_(True),
            or_(
                TelegramRecipient.telegram_user_id == telegram_user_id,
                TelegramRecipient.telegram_user_id == "",
            ),
        ]

        recipient = await session.scalar(select(TelegramRecipient).where(*recipient_filters))
        if recipient is None:
            return None
        if recipient.telegram_user_id and recipient.telegram_user_id != telegram_user_id:
            return None

        return TelegramAccessContext(
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            role=recipient.role or TelegramUserRole.RECIPIENT.value,
            username=recipient.username or "",
            first_name=recipient.first_name or "",
            is_primary=False,
        )


async def revoke_telegram_access_records(session, *, now: datetime | None = None) -> None:
    """Очищает дополнительные доступы и отзывает активные инвайты."""
    revoke_time = now or datetime.now(UTC)
    await session.execute(delete(TelegramRecipient))
    await session.execute(
        update(TelegramInvite)
        .where(TelegramInvite.used_at.is_(None), TelegramInvite.revoked_at.is_(None))
        .values(revoked_at=revoke_time)
    )


async def get_latest_active_invite(
    session,
    *,
    role: str = TelegramUserRole.RECIPIENT.value,
) -> TelegramInvite | None:
    """Возвращает последний активный инвайт."""
    now = datetime.now(UTC)
    return await session.scalar(
        select(TelegramInvite)
        .where(
            TelegramInvite.role == role,
            TelegramInvite.used_at.is_(None),
            TelegramInvite.revoked_at.is_(None),
            TelegramInvite.expires_at > now,
        )
        .order_by(TelegramInvite.created_at.desc())
        .limit(1)
    )


async def create_telegram_invite(
    session,
    *,
    role: str = TelegramUserRole.RECIPIENT.value,
    created_by_telegram_user_id: str = "",
    created_by_username: str = "",
    ttl_hours: int = INVITE_TTL_HOURS,
) -> TelegramInvite:
    """Создаёт новый одноразовый инвайт, предварительно отзывая старые активные."""
    now = datetime.now(UTC)
    await session.execute(
        update(TelegramInvite)
        .where(
            TelegramInvite.role == role,
            TelegramInvite.used_at.is_(None),
            TelegramInvite.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )

    invite = TelegramInvite(
        code=generate_telegram_code(),
        role=role,
        created_by_telegram_user_id=created_by_telegram_user_id,
        created_by_username=created_by_username,
        expires_at=now + timedelta(hours=ttl_hours),
    )
    session.add(invite)
    await session.flush()
    return invite


async def touch_poller_heartbeat(*, create_if_missing: bool = False) -> None:
    """Обновляет heartbeat poller-а в telegram_settings."""
    factory = get_session_factory()
    async with factory() as session:
        if create_if_missing:
            settings_row = await get_or_create_telegram_settings(session)
        else:
            settings_row = await session.scalar(
                select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
            )
            if settings_row is None:
                return
        settings_row.poller_heartbeat_at = datetime.now(UTC)
        await session.commit()


async def load_poller_offset() -> int | None:
    """Загружает последний обработанный offset Telegram poller-а."""
    factory = get_session_factory()
    async with factory() as session:
        settings_row = await session.scalar(
            select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
        )
        if settings_row is None:
            return None
        return settings_row.poller_offset


async def save_poller_offset(offset: int | None) -> None:
    """Сохраняет последний обработанный offset Telegram poller-а."""
    if offset is None:
        return

    factory = get_session_factory()
    async with factory() as session:
        settings_row = await get_or_create_telegram_settings(session)
        settings_row.poller_offset = offset
        await session.commit()


async def load_web_app_url() -> str:
    """Загружает web_app_url из БД (TelegramSettings) с fallback на settings.web_app_url из .env.

    Возвращает пустую строку, если URL нигде не задан.
    """
    factory = get_session_factory()
    try:
        async with factory() as session:
            row = await session.scalar(
                select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
            )
            if row and row.web_app_url:
                value = row.web_app_url.strip()
                if value:
                    return value
    except Exception:
        logger.debug("Не удалось загрузить web_app_url из БД", exc_info=True)
    return (get_settings().web_app_url or "").strip()

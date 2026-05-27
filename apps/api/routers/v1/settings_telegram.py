# -*- coding: utf-8 -*-
"""FastAPI роутер для настроек Telegram (settings_telegram).

Endpoints под /api (благодаря auto-discovery с prefix="/api"):
- GET  /settings/telegram                     — публичные поля TelegramConfig + compute
- PUT  /settings/telegram/token               — зашифровать и сохранить токен
- DELETE /settings/telegram                   — очистить токен + chat_id
- GET  /settings/telegram/recipients          — список не-revoked получателей
- DELETE /settings/telegram/recipients/{id}  — soft-delete получателя
- POST /settings/telegram/recipients/invite   — создать invite-код (TTL 24h)

БЕЗОПАСНОСТЬ: bot_token_encrypted НИКОГДА не возвращается в ответах.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import DepEngine, DepRedis
from apps.api.routers.v1.schemas.settings_telegram import (
    TelegramInviteResponse,
    TelegramRecipientResponse,
    TelegramRecipientsListResponse,
    TelegramSettingsResponse,
    TelegramTokenRequest,
)
from core.models.settings.telegram_config import TelegramConfig
from core.models.telegram.invite import TelegramInvite
from core.models.telegram.recipient import TelegramRecipient
from core.telegram.settings_compute import (
    compute_activation_command,
    compute_auth_deep_link,
    compute_bot_username,
    compute_is_authorized,
    compute_poller_status,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings/telegram", tags=["settings"])

# TTL invite-кода — 24 часа.
_INVITE_TTL_HOURS = 24


# ---------------------------------------------------------------------------
# Внутренний snapshot — безопасная передача данных вне session
# ---------------------------------------------------------------------------


@dataclass
class _ConfigSnapshot:
    """Скалярные поля TelegramConfig без ORM-ленивой загрузки."""

    bot_token_encrypted: str | None
    chat_id: int | None
    poller_heartbeat_at: datetime | None


def _snapshot(config: TelegramConfig | None) -> _ConfigSnapshot | None:
    """Считывает нужные поля из ORM-объекта ВНУТРИ session и возвращает скаляры."""
    if config is None:
        return None
    return _ConfigSnapshot(
        bot_token_encrypted=config.bot_token_encrypted,
        chat_id=config.chat_id,
        poller_heartbeat_at=config.poller_heartbeat_at,
    )


async def _load_config(session: AsyncSession) -> TelegramConfig | None:
    """Читает singleton TelegramConfig или возвращает None, если строки нет."""
    return await session.scalar(
        select(TelegramConfig).where(TelegramConfig.singleton_key == "default")
    )


async def _build_response(
    snap: _ConfigSnapshot | None,
    redis: object,
) -> TelegramSettingsResponse:
    """Строит TelegramSettingsResponse из snapshot и Redis-клиента.

    Принимает скалярный snapshot — не зависит от session.
    """
    is_authorized = compute_is_authorized(snap)
    poller_status = await compute_poller_status(snap)
    bot_username = await compute_bot_username(snap, redis)
    auth_deep_link = compute_auth_deep_link(bot_username)
    activation_command = compute_activation_command()

    # chat_id возвращаем как строку (фронт ожидает строку, BigInteger в Postgres).
    chat_id_str: str | None = None
    if snap is not None and snap.chat_id is not None:
        chat_id_str = str(snap.chat_id)

    return TelegramSettingsResponse(
        is_authorized=is_authorized,
        poller_status=poller_status,
        bot_username=bot_username,
        auth_deep_link=auth_deep_link,
        activation_command=activation_command,
        chat_id=chat_id_str,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=TelegramSettingsResponse)
async def get_telegram_settings(engine: DepEngine, redis: DepRedis) -> TelegramSettingsResponse:
    """Возвращает публичные поля TelegramConfig с compute-полями.

    НЕ возвращает bot_token_encrypted.
    """
    async with AsyncSession(engine) as session:
        config = await _load_config(session)
        snap = _snapshot(config)
    return await _build_response(snap, redis)


@router.put("/token", response_model=TelegramSettingsResponse)
async def put_telegram_token(
    body: TelegramTokenRequest,
    engine: DepEngine,
    redis: DepRedis,
) -> TelegramSettingsResponse:
    """Шифрует bot_token и сохраняет в TelegramConfig singleton.

    Если строки ещё нет — создаёт с server-defaults.
    Инвалидирует кэш bot_username в Redis.
    """
    from core.crypto import encrypt

    encrypted = encrypt(body.bot_token)

    async with AsyncSession(engine) as session:
        config = await _load_config(session)
        if config is None:
            config = TelegramConfig(bot_token_encrypted=encrypted)
            session.add(config)
        else:
            config.bot_token_encrypted = encrypted
        await session.flush()
        await session.refresh(config)
        snap = _snapshot(config)
        await session.commit()

    # Инвалидируем кэш username при смене токена.
    try:
        await redis.delete("tg:bot_username")
    except Exception as exc:
        logger.warning("Не удалось инвалидировать кэш tg:bot_username: %s", exc)

    return await _build_response(snap, redis)


@router.delete("", response_model=TelegramSettingsResponse)
async def delete_telegram_settings(engine: DepEngine, redis: DepRedis) -> TelegramSettingsResponse:
    """Очищает bot_token_encrypted и chat_id в TelegramConfig.

    Если строки нет — возвращает пустой ответ без ошибки.
    """
    async with AsyncSession(engine) as session:
        config = await _load_config(session)
        if config is not None:
            config.bot_token_encrypted = ""
            config.chat_id = None
            await session.flush()
            await session.refresh(config)
        snap = _snapshot(config)
        await session.commit()

    # Инвалидируем кэш username.
    try:
        await redis.delete("tg:bot_username")
    except Exception as exc:
        logger.warning("Не удалось инвалидировать кэш tg:bot_username: %s", exc)

    return await _build_response(snap, redis)


@router.get("/recipients", response_model=TelegramRecipientsListResponse)
async def get_telegram_recipients(engine: DepEngine) -> TelegramRecipientsListResponse:
    """Возвращает список активных (не отозванных) получателей Telegram."""
    async with AsyncSession(engine) as session:
        rows = (
            await session.scalars(
                select(TelegramRecipient).where(TelegramRecipient.revoked_at.is_(None))
            )
        ).all()
        # Считываем поля внутри session.
        recipients = [
            TelegramRecipientResponse(
                id=str(row.id),
                chat_id=row.chat_id,
                username=row.username,
                role=row.role,
                created_at=row.created_at,
            )
            for row in rows
        ]
    return TelegramRecipientsListResponse(recipients=recipients, total=len(recipients))


@router.delete("/recipients/{recipient_id}", response_model=TelegramRecipientResponse)
async def delete_telegram_recipient(
    recipient_id: str,
    engine: DepEngine,
) -> TelegramRecipientResponse:
    """Soft-delete: выставляет revoked_at = NOW() для получателя с заданным id.

    Возвращает 404, если получатель не найден или уже отозван.
    """
    try:
        r_uuid = uuid.UUID(recipient_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Неверный формат UUID") from exc

    async with AsyncSession(engine) as session:
        row = await session.get(TelegramRecipient, r_uuid)
        if row is None or row.revoked_at is not None:
            raise HTTPException(status_code=404, detail="Получатель не найден или уже удалён")
        row.revoked_at = datetime.now(UTC)
        await session.flush()
        await session.refresh(row)
        # Считываем поля внутри session.
        result = TelegramRecipientResponse(
            id=str(row.id),
            chat_id=row.chat_id,
            username=row.username,
            role=row.role,
            created_at=row.created_at,
        )
        await session.commit()

    return result


@router.post("/recipients/invite", response_model=TelegramInviteResponse)
async def post_telegram_invite(engine: DepEngine) -> TelegramInviteResponse:
    """Генерирует invite-код (TTL 24h) и сохраняет в telegram_invites.

    Возвращает { code, expires_at }.
    """
    code = secrets.token_urlsafe(16)
    expires_at = datetime.now(UTC) + timedelta(hours=_INVITE_TTL_HOURS)

    async with AsyncSession(engine) as session:
        invite = TelegramInvite(
            code=code,
            created_by="api",
            expires_at=expires_at,
        )
        session.add(invite)
        await session.flush()
        await session.refresh(invite)
        result = TelegramInviteResponse(code=code, expires_at=expires_at)
        await session.commit()

    return result

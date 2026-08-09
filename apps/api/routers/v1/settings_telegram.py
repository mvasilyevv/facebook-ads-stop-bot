# -*- coding: utf-8 -*-
"""FastAPI роутер для настроек Telegram (settings_telegram).

Endpoints под /api (благодаря auto-discovery с prefix="/api"):
- GET  /settings/telegram                     — публичные поля TelegramConfig + compute
- PUT  /settings/telegram/token               — зашифровать и сохранить токен
- DELETE /settings/telegram                   — отключить токен
- GET  /settings/telegram/recipients          — список не-revoked получателей
- DELETE /settings/telegram/recipients/{id}  — soft-delete получателя
- POST /settings/telegram/recipients/invite   — создать invite-код (TTL 24h)
- POST /settings/telegram/owner-invite        — получить/создать owner deep-link

БЕЗОПАСНОСТЬ: bot_token_encrypted НИКОГДА не возвращается в ответах.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from apps.api.deps import DepEngine, DepSettings
from apps.api.routers.v1.schemas.settings_telegram import (
    TelegramDeliveryErrorSummary,
    TelegramInviteResponse,
    TelegramNotificationDiagnosticsResponse,
    TelegramRecipientPreferenceRequest,
    TelegramRecipientPreferenceResponse,
    TelegramRecipientResponse,
    TelegramRecipientsListResponse,
    TelegramSettingsResponse,
    TelegramTokenRequest,
    TelegramWebAppUrlRequest,
)
from core.models.settings.telegram_config import TelegramConfig
from core.models.telegram.invite import TelegramInvite
from core.models.telegram.recipient import TelegramRecipient
from core.telegram.menu_button import sync_menu_buttons
from core.telegram.notifications import (
    retire_disabled_recipient_notifications_in_transaction,
    retire_revoked_recipient_backlog_in_transaction,
    serialize_recipient_delivery_state_in_transaction,
)
from core.telegram.owner_roster import lock_owner_roster
from core.telegram.settings_compute import (
    compute_activation_command,
    compute_auth_deep_link,
    compute_bot_username,
    compute_is_authorized,
)
from core.telegram.web_app_url import load_web_app_url, save_web_app_url
from core.telegram.webhook_configuration import (
    disable_token_and_schedule_webhook_deletion,
    resolve_webhook_target,
    store_rotated_token_and_schedule_webhook,
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
    is_enabled: bool
    webhook_generation: int
    credential_fingerprint: str | None


@dataclass(frozen=True)
class _InviteSnapshot:
    """Публичные поля активного invite без привязки к ORM-session."""

    code: str
    role: str
    expires_at: datetime


def _snapshot(config: TelegramConfig | None) -> _ConfigSnapshot | None:
    """Считывает нужные поля из ORM-объекта ВНУТРИ session и возвращает скаляры."""
    if config is None:
        return None
    return _ConfigSnapshot(
        bot_token_encrypted=config.bot_token_encrypted,
        is_enabled=config.is_enabled,
        webhook_generation=int(config.webhook_generation),
        credential_fingerprint=(
            bytes(config.bot_token_fingerprint).hex()
            if config.bot_token_fingerprint is not None
            else None
        ),
    )


async def _load_config(session: AsyncSession) -> TelegramConfig | None:
    """Читает singleton TelegramConfig или возвращает None, если строки нет."""
    return await session.scalar(
        select(TelegramConfig).where(TelegramConfig.singleton_key == "default")
    )


async def _load_active_owner_invite(session: AsyncSession) -> _InviteSnapshot | None:
    """Вернуть последний действующий owner-инвайт, не создавая новый на GET."""
    active_owner = await session.scalar(
        select(func.count())
        .select_from(TelegramRecipient)
        .where(
            TelegramRecipient.role == "owner",
            TelegramRecipient.revoked_at.is_(None),
        )
    )
    if int(active_owner or 0) > 0:
        return None
    invite = await session.scalar(
        select(TelegramInvite)
        .where(
            TelegramInvite.role == "owner",
            TelegramInvite.used_at.is_(None),
            TelegramInvite.revoked_at.is_(None),
            TelegramInvite.expires_at > datetime.now(UTC),
        )
        .order_by(TelegramInvite.expires_at.desc())
        .limit(1)
    )
    if invite is None:
        return None
    return _InviteSnapshot(
        code=invite.code,
        role=invite.role,
        expires_at=invite.expires_at,
    )


async def _ensure_active_owner_invite(engine: AsyncEngine) -> _InviteSnapshot:
    """Переиспользовать действующий owner-код или атомарно создать один новый.

    Транзакционный advisory-lock схлопывает параллельные клики и React retry: в БД
    остаётся один актуальный owner-инвайт вместо пачки равноценных секретов.
    """
    async with AsyncSession(engine) as session, session.begin():
        await lock_owner_roster(session)
        active_owner = await session.scalar(
            select(func.count())
            .select_from(TelegramRecipient)
            .where(
                TelegramRecipient.role == "owner",
                TelegramRecipient.revoked_at.is_(None),
            )
        )
        if int(active_owner or 0) > 0:
            raise HTTPException(
                status_code=409,
                detail="Владелец уже подключён; новые owner-ссылки запрещены",
            )
        current = await _load_active_owner_invite(session)
        if current is not None:
            return current

        invite = TelegramInvite(
            code=secrets.token_urlsafe(16),
            created_by="settings_ui",
            role="owner",
            expires_at=datetime.now(UTC) + timedelta(hours=_INVITE_TTL_HOURS),
        )
        session.add(invite)
        await session.flush()
        return _InviteSnapshot(
            code=invite.code,
            role=invite.role,
            expires_at=invite.expires_at,
        )


async def _build_response(
    engine: AsyncEngine,
    snap: _ConfigSnapshot | None,
    owner_invite: _InviteSnapshot | None,
    web_app_url: str | None = None,
    *,
    menu_sync_state: str | None = None,
) -> TelegramSettingsResponse:
    """Строит TelegramSettingsResponse из безопасного snapshot.

    Принимает скалярный snapshot — не зависит от session.
    web_app_url — DB-authoritative значение из system_config.
    """
    is_authorized = compute_is_authorized(snap)
    bot_username = await compute_bot_username(snap, engine=engine)
    invite_code = owner_invite.code if owner_invite is not None else None
    auth_deep_link = compute_auth_deep_link(bot_username, invite_code)
    activation_command = compute_activation_command(invite_code)

    return TelegramSettingsResponse(
        is_authorized=is_authorized,
        bot_username=bot_username,
        auth_deep_link=auth_deep_link,
        activation_command=activation_command,
        auth_invite_expires_at=(owner_invite.expires_at if owner_invite else None),
        web_app_url=web_app_url,
        menu_sync_state=menu_sync_state,
    )


async def _resolve_web_app_url(engine: AsyncEngine) -> str | None:
    """web_app_url читается только из DB-authoritative system_config."""
    return await load_web_app_url(engine)


async def _sync_bot_menu_button(engine: AsyncEngine, web_app_url: str) -> bool:
    """Set every Menu Button with per-call credential fencing and one deadline.

    Telegram сам не обновляет Menu Button при смене web_app_url. Поэтому при
    сохранении свежего URL сразу прописываем кнопку боту через setChatMenuButton.

    URL сохраняется отдельно, а синхронизация возвращает synced/incomplete;
    отсутствие токена и Bot API ошибки не маскируются как успешный sync.
    """
    from core.telegram.gateway import (
        TelegramFailureKind,
        TelegramGatewayError,
        TelegramHTMLGateway,
    )
    from core.telegram.notifications import (
        disable_recipient_delivery_in_transaction,
        open_telegram_auth_incident_in_transaction,
    )
    from core.telegram.outbound_authority import (
        hold_telegram_outbound_authority,
        telegram_failure_authority_is_current,
    )
    from core.telegram.service import load_telegram_config

    try:
        cfg = await load_telegram_config(engine)
    except Exception:
        logger.warning("menu button: не удалось прочитать telegram_config", exc_info=True)
        return False
    if cfg is None or not cfg.bot_token:
        logger.info("menu button: бот не настроен — пропускаю установку")
        return False

    client = TelegramHTMLGateway(bot_token=cfg.bot_token, timeout_seconds=5.0)
    try:

        async def handle_gateway_error(chat_id: int | None, exc: Exception) -> bool:
            if not isinstance(exc, TelegramGatewayError):
                return True
            if exc.kind is TelegramFailureKind.UNAUTHORIZED:
                async with engine.begin() as conn:
                    await open_telegram_auth_incident_in_transaction(
                        conn,
                        error_code="telegram_unauthorized",
                        credential_fingerprint=client.credential_fingerprint,
                        source="menu_button_sync",
                    )
                    if not await telegram_failure_authority_is_current(
                        conn,
                        bot_generation=cfg.webhook_generation,
                        credential_fingerprint=client.credential_fingerprint,
                    ):
                        await conn.rollback()
                return False
            if exc.kind is not TelegramFailureKind.FORBIDDEN:
                return True
            if chat_id is None:
                logger.warning("menu button: default scope rejected with Telegram 403")
                return True
            async with engine.begin() as conn:
                await lock_owner_roster(conn)
                recipient = (
                    await conn.execute(
                        text(
                            """
                            SELECT id, chat_id FROM telegram_recipients
                            WHERE chat_id=:chat_id AND revoked_at IS NULL
                            LIMIT 1
                            """
                        ),
                        {"chat_id": chat_id},
                    )
                ).first()
                if recipient is None:
                    return True
                recipient_id = uuid.UUID(str(recipient.id))
                await serialize_recipient_delivery_state_in_transaction(
                    conn,
                    [recipient_id],
                )
                if not await telegram_failure_authority_is_current(
                    conn,
                    bot_generation=cfg.webhook_generation,
                    credential_fingerprint=client.credential_fingerprint,
                ):
                    return False
                await disable_recipient_delivery_in_transaction(
                    conn,
                    recipient_id=recipient_id,
                    chat_id=int(recipient.chat_id),
                )
            return True

        synced = await sync_menu_buttons(
            engine,
            client,
            explicit_url=web_app_url,
            authorize_call=lambda: hold_telegram_outbound_authority(
                engine,
                bot_generation=cfg.webhook_generation,
                credential_fingerprint=client.credential_fingerprint,
            ),
            on_gateway_error=handle_gateway_error,
            deadline_seconds=10.0,
        )
        if not synced:
            logger.warning("menu button обновлён не для всех scopes")
            return False
        logger.info("menu button обновлён")
        return True
    except Exception:
        logger.warning("menu button: не удалось установить", exc_info=True)
        return False
    finally:
        try:
            await client.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=TelegramSettingsResponse)
async def get_telegram_settings(
    engine: DepEngine,
) -> TelegramSettingsResponse:
    """Возвращает публичные поля TelegramConfig с compute-полями.

    НЕ возвращает bot_token_encrypted. web_app_url — только из system_config.
    """
    async with AsyncSession(engine) as session:
        config = await _load_config(session)
        snap = _snapshot(config)
        owner_invite = await _load_active_owner_invite(session)
    web_app_url = await _resolve_web_app_url(engine)
    return await _build_response(engine, snap, owner_invite, web_app_url)


@router.put("/web-app-url", response_model=TelegramSettingsResponse)
async def put_telegram_web_app_url(
    body: TelegramWebAppUrlRequest,
    engine: DepEngine,
) -> TelegramSettingsResponse:
    """Сохраняет Web App URL Mini App в system_config (без рестарта).

    Пустая строка/None — явная очистка (DB tombstone; env не восстанавливает).
    Непустой URL обязан быть HTTPS (требование Telegram Mini Apps) → иначе 422.
    """
    cleaned = (body.web_app_url or "").strip()
    if cleaned and not cleaned.lower().startswith("https://"):
        raise HTTPException(status_code=422, detail="Web App URL должен начинаться с https://")

    await save_web_app_url(engine, cleaned or None)

    # Свежий URL → сразу прописываем Menu Button боту. Quick-tunnel меняется при
    # каждом запуске; без этого кнопка в Telegram остаётся на мёртвом старом туннеле
    # (set_chat_menu_button больше нигде не вызывается). Результат явно входит
    # в response как synced/incomplete.
    menu_sync_state = None
    if cleaned:
        menu_sync_state = "synced" if await _sync_bot_menu_button(engine, cleaned) else "incomplete"

    async with AsyncSession(engine) as session:
        config = await _load_config(session)
        snap = _snapshot(config)
        owner_invite = await _load_active_owner_invite(session)
    web_app_url = await _resolve_web_app_url(engine)
    return await _build_response(
        engine,
        snap,
        owner_invite,
        web_app_url,
        menu_sync_state=menu_sync_state,
    )


@router.put("/token", response_model=TelegramSettingsResponse)
async def put_telegram_token(
    body: TelegramTokenRequest,
    engine: DepEngine,
    settings: DepSettings,
) -> TelegramSettingsResponse:
    """Atomically persist a token and its durable desired webhook generation."""
    from core.crypto import encrypt
    from core.telegram.gateway import telegram_credential_fingerprint

    try:
        target = resolve_webhook_target(
            frontend_origin=settings.frontend_origin,
            secret_token=settings.telegram_webhook_secret,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail="Webhook URL или secret не настроены; токен не изменён",
        ) from exc
    normalized_token = body.bot_token.strip()
    if not normalized_token:
        raise HTTPException(status_code=422, detail="Telegram Bot API токен не может быть пустым")
    encrypted = encrypt(normalized_token)

    async with engine.begin() as conn:
        await store_rotated_token_and_schedule_webhook(
            conn,
            bot_token_encrypted=encrypted,
            bot_token_fingerprint=telegram_credential_fingerprint(normalized_token),
            target=target,
        )
    async with AsyncSession(engine) as session:
        config = await _load_config(session)
        snap = _snapshot(config)
        owner_invite = await _load_active_owner_invite(session)
    return await _build_response(
        engine,
        snap,
        owner_invite,
        await _resolve_web_app_url(engine),
    )


@router.delete("", response_model=TelegramSettingsResponse)
async def delete_telegram_settings(engine: DepEngine) -> TelegramSettingsResponse:
    """Disable sends now and durably delete the remote webhook before token wipe."""
    async with engine.begin() as conn:
        await disable_token_and_schedule_webhook_deletion(conn)
    async with AsyncSession(engine) as session:
        config = await _load_config(session)
        snap = _snapshot(config)
        owner_invite = await _load_active_owner_invite(session)
    return await _build_response(
        engine,
        snap,
        owner_invite,
        await _resolve_web_app_url(engine),
    )


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

    async with AsyncSession(engine) as session, session.begin():
        await lock_owner_roster(session)
        conn = await session.connection()
        # Owner roster -> recipient advisory -> recipient row -> delivery rows.
        # Enqueue/finalization uses the same recipient-before-row-work order, so
        # the recipient FK checks cannot form a row/advisory deadlock here.
        await serialize_recipient_delivery_state_in_transaction(conn, [r_uuid])
        row = await session.scalar(
            select(TelegramRecipient).where(TelegramRecipient.id == r_uuid).with_for_update()
        )
        if row is None or row.revoked_at is not None:
            raise HTTPException(status_code=404, detail="Получатель не найден или уже удалён")
        if row.role == "owner":
            active_owner_count = await session.scalar(
                select(func.count())
                .select_from(TelegramRecipient)
                .where(
                    TelegramRecipient.role == "owner",
                    TelegramRecipient.revoked_at.is_(None),
                )
            )
            if int(active_owner_count or 0) <= 1:
                raise HTTPException(
                    status_code=409,
                    detail="Нельзя отозвать последнего активного владельца",
                )
        row.revoked_at = datetime.now(UTC)
        await session.flush()
        await retire_revoked_recipient_backlog_in_transaction(
            conn,
            recipient_id=row.id,
            chat_id=row.chat_id,
        )
        await session.refresh(row)
        # Считываем поля внутри session.
        result = TelegramRecipientResponse(
            id=str(row.id),
            chat_id=row.chat_id,
            username=row.username,
            role=row.role,
            created_at=row.created_at,
        )

    return result


async def _ensure_active_recipient(engine: AsyncEngine, recipient_id: uuid.UUID) -> None:
    async with engine.connect() as conn:
        exists = (
            await conn.execute(
                text(
                    """
                    SELECT 1 FROM telegram_recipients
                    WHERE id = :recipient_id AND revoked_at IS NULL
                    """
                ),
                {"recipient_id": recipient_id},
            )
        ).first()
    if exists is None:
        raise HTTPException(status_code=404, detail="Получатель не найден или уже удалён")


@router.get(
    "/recipients/{recipient_id}/preferences",
    response_model=TelegramRecipientPreferenceResponse,
)
async def get_telegram_recipient_preferences(
    recipient_id: uuid.UUID,
    engine: DepEngine,
) -> TelegramRecipientPreferenceResponse:
    await _ensure_active_recipient(engine, recipient_id)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT timezone, min_severity, quiet_hours_start, quiet_hours_end,
                           digest_local_time, categories, is_enabled, updated_at
                    FROM telegram_recipient_preferences
                    WHERE recipient_id = :recipient_id
                    """
                ),
                {"recipient_id": recipient_id},
            )
        ).first()
    if row is None:
        return TelegramRecipientPreferenceResponse(recipient_id=str(recipient_id))
    return TelegramRecipientPreferenceResponse(
        recipient_id=str(recipient_id),
        timezone=str(row.timezone),
        min_severity=str(row.min_severity),
        quiet_hours_start=row.quiet_hours_start,
        quiet_hours_end=row.quiet_hours_end,
        digest_local_time=row.digest_local_time,
        categories=dict(row.categories or {}),
        is_enabled=bool(row.is_enabled),
        updated_at=row.updated_at,
    )


@router.put(
    "/recipients/{recipient_id}/preferences",
    response_model=TelegramRecipientPreferenceResponse,
)
async def put_telegram_recipient_preferences(
    recipient_id: uuid.UUID,
    body: TelegramRecipientPreferenceRequest,
    engine: DepEngine,
) -> TelegramRecipientPreferenceResponse:
    async with engine.begin() as conn:
        await serialize_recipient_delivery_state_in_transaction(conn, [recipient_id])
        active_recipient = await conn.scalar(
            text(
                """
                SELECT 1
                FROM telegram_recipients
                WHERE id = :recipient_id AND revoked_at IS NULL
                """
            ),
            {"recipient_id": recipient_id},
        )
        if active_recipient is None:
            raise HTTPException(
                status_code=404,
                detail="Получатель не найден или уже удалён",
            )
        row = (
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_recipient_preferences
                        (recipient_id, timezone, min_severity,
                         quiet_hours_start, quiet_hours_end, digest_local_time,
                         categories, is_enabled)
                    VALUES
                        (:recipient_id, :timezone, :min_severity,
                         :quiet_start, :quiet_end, :digest_time,
                         CAST(:categories AS JSONB), :is_enabled)
                    ON CONFLICT (recipient_id) DO UPDATE
                    SET timezone = EXCLUDED.timezone,
                        min_severity = EXCLUDED.min_severity,
                        quiet_hours_start = EXCLUDED.quiet_hours_start,
                        quiet_hours_end = EXCLUDED.quiet_hours_end,
                        digest_local_time = EXCLUDED.digest_local_time,
                        categories = EXCLUDED.categories,
                        is_enabled = EXCLUDED.is_enabled,
                        updated_at = NOW()
                    RETURNING timezone, min_severity, quiet_hours_start,
                              quiet_hours_end, digest_local_time, categories,
                              is_enabled, updated_at
                    """
                ),
                {
                    "recipient_id": recipient_id,
                    "timezone": body.timezone,
                    "min_severity": body.min_severity,
                    "quiet_start": body.quiet_hours_start,
                    "quiet_end": body.quiet_hours_end,
                    "digest_time": body.digest_local_time,
                    "categories": json.dumps(body.categories),
                    "is_enabled": body.is_enabled,
                },
            )
        ).one()
        if not body.is_enabled:
            await retire_disabled_recipient_notifications_in_transaction(
                conn,
                recipient_id=recipient_id,
            )
    return TelegramRecipientPreferenceResponse(
        recipient_id=str(recipient_id),
        timezone=str(row.timezone),
        min_severity=str(row.min_severity),
        quiet_hours_start=row.quiet_hours_start,
        quiet_hours_end=row.quiet_hours_end,
        digest_local_time=row.digest_local_time,
        categories=dict(row.categories or {}),
        is_enabled=bool(row.is_enabled),
        updated_at=row.updated_at,
    )


@router.get(
    "/diagnostics",
    response_model=TelegramNotificationDiagnosticsResponse,
)
async def get_telegram_notification_diagnostics(
    engine: DepEngine,
) -> TelegramNotificationDiagnosticsResponse:
    async with engine.connect() as conn:
        delivery_state_rows = (
            await conn.execute(
                text(
                    """
                    SELECT state, COUNT(*)::bigint AS count
                    FROM notification_deliveries
                    GROUP BY state
                    """
                )
            )
        ).all()
        inbox_state_rows = (
            await conn.execute(
                text(
                    """
                    SELECT state, COUNT(*)::bigint AS count
                    FROM telegram_updates_inbox
                    GROUP BY state
                    """
                )
            )
        ).all()
        command_reply_state_rows = (
            await conn.execute(
                text(
                    """
                    SELECT state, COUNT(*)::bigint AS count
                    FROM telegram_command_replies
                    GROUP BY state
                    """
                )
            )
        ).all()
        summary = (
            await conn.execute(
                text(
                    """
                    SELECT
                        MIN(d.created_at) FILTER (
                            WHERE d.state IN ('pending','retry','leased')
                        ) AS oldest_pending_at,
                        COUNT(DISTINCT r.id) FILTER (
                            WHERE r.revoked_at IS NULL
                        )::bigint AS active_recipients,
                        COUNT(DISTINCT r.id) FILTER (
                            WHERE r.revoked_at IS NULL
                              AND COALESCE(p.is_enabled, TRUE)
                        )::bigint AS enabled_recipients,
                        EXISTS (
                            SELECT 1 FROM incidents i
                            WHERE i.incident_key = 'telegram:bot-auth'
                              AND i.status IN ('open','acknowledged','executing')
                        ) AS auth_incident_active,
                        EXISTS (
                            SELECT 1 FROM telegram_config c
                            WHERE c.singleton_key = 'default'
                              AND c.bot_token_encrypted <> ''
                              AND c.is_enabled
                        ) AS gateway_configured,
                        (SELECT c.webhook_state
                           FROM telegram_config c
                          WHERE c.singleton_key = 'default') AS webhook_state,
                        (SELECT c.webhook_generation
                           FROM telegram_config c
                          WHERE c.singleton_key = 'default') AS webhook_generation,
                        (SELECT c.webhook_applied_generation
                           FROM telegram_config c
                          WHERE c.singleton_key = 'default')
                            AS webhook_applied_generation,
                        (SELECT c.webhook_desired_url
                           FROM telegram_config c
                          WHERE c.singleton_key = 'default') AS webhook_desired_url,
                        (SELECT c.webhook_remote_url
                           FROM telegram_config c
                          WHERE c.singleton_key = 'default') AS webhook_remote_url,
                        (SELECT c.webhook_secret_digest IS NOT NULL
                           FROM telegram_config c
                          WHERE c.singleton_key = 'default')
                            AS webhook_secret_digest_present,
                        (SELECT c.webhook_remote_pending_update_count
                           FROM telegram_config c
                          WHERE c.singleton_key = 'default')
                            AS webhook_remote_pending_update_count,
                        (SELECT c.webhook_remote_last_error_at
                           FROM telegram_config c
                          WHERE c.singleton_key = 'default')
                            AS webhook_remote_last_error_at,
                        (SELECT c.webhook_remote_last_error_message
                           FROM telegram_config c
                          WHERE c.singleton_key = 'default')
                            AS webhook_remote_last_error_message,
                        (SELECT c.webhook_checked_at
                           FROM telegram_config c
                          WHERE c.singleton_key = 'default') AS webhook_checked_at,
                        (SELECT c.webhook_configured_at
                           FROM telegram_config c
                          WHERE c.singleton_key = 'default') AS webhook_configured_at,
                        (SELECT c.webhook_last_error_code
                           FROM telegram_config c
                          WHERE c.singleton_key = 'default')
                            AS webhook_last_error_code,
                        (SELECT c.webhook_last_error_detail
                           FROM telegram_config c
                          WHERE c.singleton_key = 'default')
                            AS webhook_last_error_detail,
                        (SELECT MAX(u.received_at) FROM telegram_updates_inbox u)
                            AS last_webhook_update_at,
                        (SELECT MIN(u.received_at)
                           FROM telegram_updates_inbox u
                          WHERE u.state IN ('pending','retry','leased'))
                            AS oldest_inbox_pending_at,
                        (SELECT MIN(c.created_at)
                           FROM telegram_command_replies c
                          WHERE c.state IN ('pending','retry','leased'))
                            AS oldest_command_pending_at
                    FROM telegram_recipients r
                    LEFT JOIN telegram_recipient_preferences p ON p.recipient_id = r.id
                    LEFT JOIN notification_deliveries d ON d.recipient_id = r.id
                    """
                )
            )
        ).one()
        error_rows = (
            await conn.execute(
                text(
                    """
                    SELECT d.id, d.state, d.last_error_code, d.updated_at,
                           e.correlation_id
                    FROM notification_deliveries d
                    JOIN notification_events e ON e.id = d.event_id
                    WHERE d.last_error_code IS NOT NULL
                    ORDER BY d.updated_at DESC, d.id DESC
                    LIMIT 20
                    """
                )
            )
        ).all()
        as_of = (await conn.execute(text("SELECT clock_timestamp()"))).scalar_one()

    delivery_counts = {str(row.state): int(row.count) for row in delivery_state_rows}
    inbox_counts = {str(row.state): int(row.count) for row in inbox_state_rows}
    command_reply_counts = {str(row.state): int(row.count) for row in command_reply_state_rows}
    active_states = {"pending", "retry", "leased"}
    error_states = {"dead", "unknown"}
    all_counts = (delivery_counts, inbox_counts, command_reply_counts)
    has_errors = any(counts.get(state, 0) > 0 for counts in all_counts for state in error_states)
    has_active = any(counts.get(state, 0) > 0 for counts in all_counts for state in active_states)
    outbox_state = "degraded" if has_errors else "active" if has_active else "idle"
    oldest_pending_at = min(
        (
            value
            for value in (
                summary.oldest_pending_at,
                summary.oldest_inbox_pending_at,
                summary.oldest_command_pending_at,
            )
            if value is not None
        ),
        default=None,
    )
    auth_incident_active = bool(summary.auth_incident_active)
    gateway_state = (
        "unconfigured"
        if not bool(summary.gateway_configured)
        else "auth_error"
        if auth_incident_active
        else "configured"
    )
    known_webhook_states = {
        "unconfigured",
        "pending",
        "applying",
        "retry",
        "configured",
        "failed",
    }
    stored_webhook_state = str(summary.webhook_state or "unconfigured")
    if stored_webhook_state not in known_webhook_states:
        stored_webhook_state = "failed"
    webhook_remote_url_matches = bool(
        summary.webhook_desired_url and summary.webhook_remote_url == summary.webhook_desired_url
    )
    configured_invariants = bool(
        stored_webhook_state == "configured"
        and summary.webhook_applied_generation == summary.webhook_generation
        and summary.webhook_secret_digest_present
        and webhook_remote_url_matches
        and not summary.webhook_last_error_code
    )
    effective_webhook_state = (
        "failed"
        if stored_webhook_state == "configured" and not configured_invariants
        else stored_webhook_state
    )

    return TelegramNotificationDiagnosticsResponse(
        as_of=as_of,
        webhook_state=effective_webhook_state,
        webhook_generation=int(summary.webhook_generation or 0),
        webhook_applied_generation=summary.webhook_applied_generation,
        webhook_desired_url=summary.webhook_desired_url,
        webhook_remote_url=summary.webhook_remote_url,
        webhook_remote_url_matches=webhook_remote_url_matches,
        webhook_secret_digest_present=bool(summary.webhook_secret_digest_present),
        webhook_remote_pending_update_count=(summary.webhook_remote_pending_update_count),
        webhook_remote_last_error_at=summary.webhook_remote_last_error_at,
        webhook_remote_last_error_message=(summary.webhook_remote_last_error_message),
        webhook_checked_at=summary.webhook_checked_at,
        webhook_configured_at=summary.webhook_configured_at,
        webhook_last_error_code=summary.webhook_last_error_code,
        webhook_last_error_detail=summary.webhook_last_error_detail,
        gateway_state=gateway_state,
        outbox_state=outbox_state,
        last_webhook_update_at=summary.last_webhook_update_at,
        inbox_counts=inbox_counts,
        delivery_counts=delivery_counts,
        command_reply_counts=command_reply_counts,
        oldest_pending_at=oldest_pending_at,
        active_recipients=int(summary.active_recipients or 0),
        enabled_recipients=int(summary.enabled_recipients or 0),
        auth_incident_active=auth_incident_active,
        recent_errors=[
            TelegramDeliveryErrorSummary(
                delivery_id=int(row.id),
                state=str(row.state),
                error_code=str(row.last_error_code),
                updated_at=row.updated_at,
                correlation_id=str(row.correlation_id),
            )
            for row in error_rows
        ],
    )


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
        result = TelegramInviteResponse(
            code=code,
            expires_at=expires_at,
            role=invite.role,
            activation_command=compute_activation_command(code) or "",
        )
        await session.commit()

    return result


@router.post("/owner-invite", response_model=TelegramInviteResponse)
async def post_telegram_owner_invite(
    engine: DepEngine,
) -> TelegramInviteResponse:
    """Вернуть действующую owner-ссылку или создать её атомарно.

    Повторный клик и сетевой retry возвращают тот же код до его использования или
    истечения. GET настроек никогда не создаёт секреты сам — он только показывает
    уже существующую ссылку.
    """
    invite = await _ensure_active_owner_invite(engine)
    async with AsyncSession(engine) as session:
        config = await _load_config(session)
        snap = _snapshot(config)
    bot_username = await compute_bot_username(snap, engine=engine)
    activation_command = compute_activation_command(invite.code)
    if activation_command is None:  # pragma: no cover - generated code is non-empty
        raise HTTPException(status_code=500, detail="Не удалось создать команду подключения")
    return TelegramInviteResponse(
        code=invite.code,
        expires_at=invite.expires_at,
        role=invite.role,
        auth_deep_link=compute_auth_deep_link(bot_username, invite.code),
        activation_command=activation_command,
    )

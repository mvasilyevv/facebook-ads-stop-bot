# -*- coding: utf-8 -*-
"""FastAPI роутер для настроек Vision браузера и получателей Telegram."""

import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_db
from apps.api.schemas import (
    InviteCodeResponse,
    TelegramRecipientSchema,
    VisionSettingsSchema,
    VisionSettingsUpdateSchema,
)
from core.browser.vision_client import VisionClient
from core.crypto import decrypt, encrypt
from core.domain import TelegramUserRole
from core.models import TelegramRecipient, TelegramSettings, VisionSettings
from core.telegram.service import (
    CONTROL_TOPIC_NAME,
    create_telegram_invite,
    forum_topics_ready,
    is_forum_delivery_mode,
    mask_chat_id,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["vision", "telegram"])


async def get_vision_settings(db: AsyncSession = Depends(get_db)):
    """Получить настройки Vision браузера (токен маскируется)."""
    result = await db.execute(
        select(VisionSettings).where(VisionSettings.singleton_key == "default")
    )
    row = result.scalar_one_or_none()
    if row is None:
        return VisionSettingsSchema()
    return VisionSettingsSchema(
        api_url=row.api_url,
        x_token="",  # Никогда не возвращаем расшифрованный токен
        profile_id=row.profile_id,
        has_token=bool(row.x_token_encrypted),
    )


@router.put("/settings/vision", response_model=VisionSettingsSchema)
async def update_vision_settings(
    body: VisionSettingsUpdateSchema, db: AsyncSession = Depends(get_db)
):
    """Обновить настройки Vision браузера."""
    result = await db.execute(
        select(VisionSettings).where(VisionSettings.singleton_key == "default")
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = VisionSettings(singleton_key="default")
        db.add(row)
    row.api_url = body.api_url
    if body.x_token:
        row.x_token_encrypted = encrypt(body.x_token)
    row.profile_id = body.profile_id
    await db.commit()
    return VisionSettingsSchema(
        api_url=row.api_url,
        x_token="",
        profile_id=row.profile_id,
        has_token=bool(row.x_token_encrypted),
    )


@router.post("/vision/reconnect")
async def vision_reconnect(db: AsyncSession = Depends(get_db)):
    """Немедленно перезапустить профиль Vision и попросить observer переподключиться."""
    from apps.api.routers.settings import (
        _start_observer_process,
        _stop_observer_process,
    )

    result = await db.execute(
        select(VisionSettings).where(VisionSettings.singleton_key == "default")
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=400, detail="Настройки Vision ещё не сохранены")

    if not row.x_token_encrypted:
        raise HTTPException(status_code=400, detail="Vision X-Token не настроен")
    if not row.profile_id:
        raise HTTPException(status_code=400, detail="Не выбран профиль Vision")

    x_token = decrypt(row.x_token_encrypted)
    if not x_token:
        raise HTTPException(status_code=400, detail="Не удалось расшифровать Vision X-Token")

    old_observer_pid = await _stop_observer_process()
    client = VisionClient(x_token=x_token, base_url=row.api_url)
    profile_port: int | None = None
    new_observer_pid: int | None = None
    reconnect_error: HTTPException | None = None

    try:
        folder_id = await client.resolve_folder_id(row.profile_id)
        try:
            await client.stop_profile(folder_id, row.profile_id)
        except Exception:
            # Профиль мог уже быть остановлен, это не должно ломать повторный старт.
            pass

        stopped = await client.wait_until_profile_stopped(row.profile_id)
        if not stopped:
            raise RuntimeError(f"Vision не остановил профиль {row.profile_id} после команды stop")
        profile = await client.start_profile(folder_id, row.profile_id)
        profile_port = profile.port
        row.reconnect_requested = True
        await db.commit()
    except HTTPException as exc:
        reconnect_error = exc
    except Exception as exc:
        reconnect_error = HTTPException(
            status_code=502,
            detail=f"Не удалось перезапустить профиль Vision: {exc}",
        )
    finally:
        await client.close()
        new_observer_pid = await _start_observer_process(
            reason="Ручное переподключение Vision через UI"
        )

    if reconnect_error is not None:
        raise reconnect_error

    if profile_port is not None:
        return {
            "ok": True,
            "message": (
                "Observer был временно остановлен, профиль Vision перезапущен, "
                "воркер запущен заново."
            ),
            "port": profile_port,
            "old_observer_pid": old_observer_pid,
            "new_observer_pid": new_observer_pid,
        }

    return {
        "ok": True,
        "message": (
            "Observer был перезапущен, профиль Vision тоже перезапущен, "
            "но CDP-порт пока не появился."
        ),
        "old_observer_pid": old_observer_pid,
        "new_observer_pid": new_observer_pid,
    }


@router.get("/vision/profiles")
async def get_vision_profiles(db: AsyncSession = Depends(get_db)):
    """Получить список профилей Vision (проксируем запрос к Vision API)."""
    import httpx

    result = await db.execute(
        select(VisionSettings).where(VisionSettings.singleton_key == "default")
    )
    row = result.scalar_one_or_none()
    if row is None or not row.x_token_encrypted:
        raise HTTPException(status_code=400, detail="Vision X-Token не настроен")

    x_token = decrypt(row.x_token_encrypted)
    if not x_token:
        raise HTTPException(status_code=400, detail="Не удалось расшифровать Vision X-Token")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{row.api_url.rstrip('/')}/list",
                headers={"X-Token": x_token},
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Vision API вернул {resp.status_code}")
        data = resp.json()
        # Vision API возвращает {"profiles": [...]} (словарь, не список)
        raw = data.get("profiles") if isinstance(data, dict) else data
        profiles = []
        for item in raw if isinstance(raw, list) else []:
            profiles.append(
                {
                    "folder_id": item.get("folder_id", ""),
                    "profile_id": item.get("profile_id", ""),
                    "name": item.get("name") or item.get("profile_id", ""),
                    "port": item.get("port"),
                }
            )
        return profiles
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=502, detail=f"Не удалось подключиться к Vision API: {e}"
        ) from e


# ==========================================
# Эндпоинты — Telegram получатели (мультипользователи)
# ==========================================


@router.get("/settings/telegram/recipients", response_model=list[TelegramRecipientSchema])
async def list_telegram_recipients(db: AsyncSession = Depends(get_db)):
    """Список дополнительных получателей Telegram-уведомлений."""
    result = await db.execute(
        select(TelegramRecipient).order_by(TelegramRecipient.created_at.asc())
    )
    recipients = result.scalars().all()
    return [
        TelegramRecipientSchema(
            id=str(r.id),
            chat_id=r.chat_id,
            masked_chat_id=mask_chat_id(r.chat_id),
            telegram_user_id=r.telegram_user_id,
            username=r.username,
            first_name=r.first_name,
            role=r.role or TelegramUserRole.RECIPIENT.value,
            is_active=r.is_active,
            created_at=r.created_at.isoformat(),
        )
        for r in recipients
    ]


@router.delete("/settings/telegram/recipients/{recipient_id}")
async def delete_telegram_recipient(recipient_id: str, db: AsyncSession = Depends(get_db)):
    """Удалить получателя Telegram-уведомлений."""
    result = await db.execute(
        select(TelegramRecipient).where(TelegramRecipient.id == _uuid.UUID(recipient_id))
    )
    recipient = result.scalar_one_or_none()
    if recipient is None:
        raise HTTPException(status_code=404, detail="Получатель не найден")
    await db.delete(recipient)
    await db.commit()
    return {"ok": True}


@router.post("/settings/telegram/recipients/invite", response_model=InviteCodeResponse)
async def create_invite_code(db: AsyncSession = Depends(get_db)):
    """Сгенерировать одноразовый код для добавления нового получателя."""
    from apps.api.routers.settings import _activation_command

    row = await db.scalar(
        select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
    )
    if row is None or not row.is_authorized:
        raise HTTPException(status_code=400, detail="Telegram-бот не настроен")
    if not is_forum_delivery_mode(getattr(row, "delivery_mode", None)):
        raise HTTPException(
            status_code=400, detail="Инвайты доступны только после cutover в группу"
        )
    if not forum_topics_ready(row):
        raise HTTPException(status_code=400, detail="Forum topics ещё не готовы")

    invite = await create_telegram_invite(
        db,
        role=TelegramUserRole.RECIPIENT.value,
        created_by_telegram_user_id=row.owner_telegram_user_id or "",
        created_by_username=row.owner_username or "",
    )
    await db.commit()

    return InviteCodeResponse(
        code=invite.code,
        bot_username=row.bot_username or "",
        role=invite.role or TelegramUserRole.RECIPIENT.value,
        expires_at=invite.expires_at.isoformat() if invite.expires_at else None,
        deep_link="",
        activation_command=_activation_command(invite.code),
        activation_target=CONTROL_TOPIC_NAME,
    )

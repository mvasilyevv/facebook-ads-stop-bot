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
from core.config import get_settings
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


async def _resolve_single_vision_profile_id(api_url: str, x_token: str) -> str | None:
    """Возвращает единственный profile_id из Vision, если профиль ровно один."""
    if not x_token:
        return None

    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{api_url.rstrip('/')}/list",
                headers={"X-Token": x_token},
            )
        if resp.status_code != 200:
            logger.warning("Vision API вернул %s при авто-выборе профиля", resp.status_code)
            return None
        data = resp.json()
    except httpx.RequestError:
        logger.warning("Не удалось подключиться к Vision API для авто-выбора профиля")
        return None
    except ValueError:
        logger.warning("Vision API вернул некорректный JSON при авто-выборе профиля")
        return None

    raw = data.get("profiles") if isinstance(data, dict) else data
    profiles = [
        item
        for item in (raw if isinstance(raw, list) else [])
        if isinstance(item, dict) and item.get("profile_id")
    ]
    if len(profiles) != 1:
        return None
    return str(profiles[0]["profile_id"])


@router.get("/settings/vision", response_model=VisionSettingsSchema)
async def get_vision_settings(db: AsyncSession = Depends(get_db)):
    """Получить настройки Vision браузера (токен маскируется)."""
    settings = get_settings()
    result = await db.execute(
        select(VisionSettings).where(VisionSettings.singleton_key == "default")
    )
    row = result.scalar_one_or_none()
    if row is None:
        return VisionSettingsSchema(
            auto_restart_on_missing_cdp=settings.vision_auto_restart_on_missing_cdp
        )
    return VisionSettingsSchema(
        api_url=row.api_url,
        x_token="",  # Никогда не возвращаем расшифрованный токен
        profile_id=row.profile_id,
        has_token=bool(row.x_token_encrypted),
        auto_restart_on_missing_cdp=settings.vision_auto_restart_on_missing_cdp,
    )


@router.put("/settings/vision", response_model=VisionSettingsSchema)
async def update_vision_settings(
    body: VisionSettingsUpdateSchema, db: AsyncSession = Depends(get_db)
):
    """Обновить настройки Vision браузера."""
    settings = get_settings()
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

    requested_profile_id = body.profile_id.strip()
    if requested_profile_id:
        row.profile_id = requested_profile_id
    else:
        x_token = body.x_token or decrypt(row.x_token_encrypted or "")
        single_profile_id = await _resolve_single_vision_profile_id(row.api_url, x_token)
        if single_profile_id:
            row.profile_id = single_profile_id

    await db.commit()
    return VisionSettingsSchema(
        api_url=row.api_url,
        x_token="",
        profile_id=row.profile_id,
        has_token=bool(row.x_token_encrypted),
        auto_restart_on_missing_cdp=settings.vision_auto_restart_on_missing_cdp,
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

    # Прямые HTTP-запросы к Vision API (без VisionClient)
    async def _vision_request(path: str, method: str = "GET") -> dict | None:
        import httpx

        url = f"{row.api_url}{path}"
        async with httpx.AsyncClient() as http_client:
            resp = await http_client.request(method, url, headers={"X-Token": x_token})
            if resp.status_code >= 400:
                raise HTTPException(status_code=502, detail=f"Vision API error: {resp.text}")
            return resp.json() if resp.content else None

    async def _resolve_folder_id() -> str:
        data = await _vision_request("/list")
        profiles = data.get("profiles", []) if data else []
        for p in profiles:
            if p.get("profile_id") == row.profile_id:
                return p["folder_id"]
        raise HTTPException(status_code=404, detail=f"Profile {row.profile_id} not found")

    async def _stop_profile(folder_id: str) -> None:
        try:
            await _vision_request(f"/stop/{folder_id}/{row.profile_id}")
        except HTTPException:
            pass  # Profile may already be stopped

    async def _wait_stopped(timeout_secs: float = 15.0) -> bool:
        import asyncio

        deadline = __import__("time").time() + timeout_secs
        while __import__("time").time() < deadline:
            data = await _vision_request("/list")
            profiles = data.get("profiles", []) if data else []
            if not any(p.get("profile_id") == row.profile_id for p in profiles):
                return True
            await asyncio.sleep(1.0)
        return False

    async def _start_profile(folder_id: str) -> int | None:
        import asyncio

        data = await _vision_request(f"/start/{folder_id}/{row.profile_id}")
        port = data.get("port") or data.get("cdp_port") if data else None
        if port:
            return port
        # Poll for port
        deadline = __import__("time").time() + 15.0
        while __import__("time").time() < deadline:
            data = await _vision_request("/list")
            profiles = data.get("profiles", []) if data else []
            for p in profiles:
                if p.get("profile_id") == row.profile_id and p.get("port"):
                    return p["port"]
            await asyncio.sleep(1.0)
        return None

    profile_port: int | None = None
    new_observer_pid: int | None = None
    reconnect_error: HTTPException | None = None

    try:
        folder_id = await _resolve_folder_id()
        await _stop_profile(folder_id)

        stopped = await _wait_stopped()
        if not stopped:
            raise RuntimeError(f"Vision не остановил профиль {row.profile_id} после команды stop")
        profile_port = await _start_profile(folder_id)
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

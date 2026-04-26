# -*- coding: utf-8 -*-
"""FastAPI роутер для настроек Vision браузера и получателей Telegram."""

import asyncio
import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_db
from apps.api.schemas import (
    InviteCodeResponse,
    TelegramRecipientSchema,
    VisionCdpEnsureResponseSchema,
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

_VISION_HTTP_TIMEOUT_SECONDS = 10.0
_CDP_READY_TIMEOUT_SECONDS = 2.0
_VISION_RECOVERY_STOP_TIMEOUT_SECONDS = 20.0
_VISION_RECOVERY_PORT_TIMEOUT_SECONDS = 20.0
_VISION_RECOVERY_SETTLE_SECONDS = 1.0


def _coerce_optional_int(value: object) -> int | None:
    """Приводит значение Vision API к int или возвращает None."""
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


async def _vision_request(api_url: str, x_token: str, path: str) -> dict | None:
    """Выполняет локальный запрос к Vision API с единым форматом ошибок."""
    import httpx

    url = f"{api_url.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=_VISION_HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.get(url, headers={"X-Token": x_token})
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Не удалось подключиться к Vision API: {exc}",
        ) from exc

    if resp.status_code == 401:
        raise HTTPException(
            status_code=401,
            detail=(
                "Vision X-Token недействителен или истёк. "
                "Обновите X-Token в настройках Vision и повторите перезапуск профиля. "
                f"Ответ Vision: {resp.text}"
            ),
        )
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"Vision API вернул ошибку {resp.status_code}: {resp.text}",
        )
    return resp.json() if resp.content else None


def _extract_vision_profiles(data: dict | None) -> list[dict]:
    """Достаёт список профилей из ответа Vision /list."""
    raw = data.get("profiles") if isinstance(data, dict) else data
    return [
        item
        for item in (raw if isinstance(raw, list) else [])
        if isinstance(item, dict) and item.get("profile_id")
    ]


def _find_vision_profile(data: dict | None, profile_id: str) -> dict | None:
    """Находит профиль Vision в ответе /list."""
    for profile in _extract_vision_profiles(data):
        if profile.get("profile_id") == profile_id:
            return profile
    return None


async def _is_cdp_ready(port: int) -> bool:
    """Проверяет, отвечает ли CDP endpoint выбранного профиля."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=_CDP_READY_TIMEOUT_SECONDS) as client:
            resp = await client.get(f"http://127.0.0.1:{port}/json/version")
        if resp.status_code != 200:
            return False
        data = resp.json()
    except (httpx.RequestError, ValueError):
        return False
    return bool(data.get("webSocketDebuggerUrl"))


async def _wait_cdp_ready(port: int, timeout_seconds: float = 20.0) -> bool:
    """Ждёт готовности CDP endpoint после запуска профиля Vision."""
    deadline = __import__("time").time() + timeout_seconds
    while __import__("time").time() < deadline:
        if await _is_cdp_ready(port):
            return True
        await asyncio.sleep(1.0)
    return False


async def _build_vision_runtime_status(
    *,
    api_url: str,
    x_token: str,
    profile_id: str,
) -> dict[str, object]:
    """Собирает read-only runtime-статус профиля Vision для UI."""
    if not x_token or not profile_id:
        return {
            "runtime_status": "NOT_CONFIGURED",
            "runtime_status_message": "Vision X-Token или профиль ещё не настроены.",
            "profile_running": False,
            "cdp_port": None,
            "cdp_ready": False,
            "folder_id": None,
        }

    try:
        data = await _vision_request(api_url, x_token, "/list")
    except HTTPException as exc:
        runtime_status = "INVALID_TOKEN" if exc.status_code == 401 else "API_UNAVAILABLE"
        return {
            "runtime_status": runtime_status,
            "runtime_status_message": str(exc.detail),
            "profile_running": False,
            "cdp_port": None,
            "cdp_ready": False,
            "folder_id": None,
        }

    profile = _find_vision_profile(data, profile_id)
    if profile is None:
        return {
            "runtime_status": "NOT_RUNNING",
            "runtime_status_message": "Профиль Vision не запущен. Он стартует при первом скане или обращении к браузеру.",
            "profile_running": False,
            "cdp_port": None,
            "cdp_ready": False,
            "folder_id": None,
        }

    port = _coerce_optional_int(profile.get("port") or profile.get("cdp_port"))
    if port is None:
        return {
            "runtime_status": "MISSING_CDP",
            "runtime_status_message": "Профиль Vision запущен, но CDP-порт не появился.",
            "profile_running": True,
            "cdp_port": None,
            "cdp_ready": False,
            "folder_id": profile.get("folder_id"),
        }

    cdp_ready = await _is_cdp_ready(port)
    if not cdp_ready:
        return {
            "runtime_status": "CDP_NOT_READY",
            "runtime_status_message": f"CDP-порт {port} есть, но endpoint пока не отвечает.",
            "profile_running": True,
            "cdp_port": port,
            "cdp_ready": False,
            "folder_id": profile.get("folder_id"),
        }

    return {
        "runtime_status": "READY",
        "runtime_status_message": f"Профиль Vision подключён, CDP-порт {port} готов.",
        "profile_running": True,
        "cdp_port": port,
        "cdp_ready": True,
        "folder_id": profile.get("folder_id"),
    }


async def _wait_vision_profile_stopped(
    *,
    api_url: str,
    x_token: str,
    profile_id: str,
    timeout_seconds: float = _VISION_RECOVERY_STOP_TIMEOUT_SECONDS,
) -> bool:
    """Ждёт, пока профиль исчезнет из списка запущенных профилей Vision."""
    deadline = __import__("time").time() + timeout_seconds
    while __import__("time").time() < deadline:
        data = await _vision_request(api_url, x_token, "/list")
        if _find_vision_profile(data, profile_id) is None:
            return True
        await asyncio.sleep(1.0)
    return False


async def _wait_vision_profile_port(
    *,
    api_url: str,
    x_token: str,
    profile_id: str,
    timeout_seconds: float = _VISION_RECOVERY_PORT_TIMEOUT_SECONDS,
) -> int | None:
    """Ждёт появления CDP-порта у запущенного профиля Vision."""
    deadline = __import__("time").time() + timeout_seconds
    while __import__("time").time() < deadline:
        data = await _vision_request(api_url, x_token, "/list")
        profile = _find_vision_profile(data, profile_id)
        port = _coerce_optional_int(
            profile.get("port") or profile.get("cdp_port") if profile else None
        )
        if port is not None:
            return port
        await asyncio.sleep(1.0)
    return None


async def _start_vision_profile(
    *,
    api_url: str,
    x_token: str,
    folder_id: str,
    profile_id: str,
) -> int | None:
    """Запускает профиль Vision и возвращает CDP-порт, если он появился."""
    data = await _vision_request(api_url, x_token, f"/start/{folder_id}/{profile_id}")
    port = _coerce_optional_int(data.get("port") or data.get("cdp_port") if data else None)
    if port is not None:
        return port
    return await _wait_vision_profile_port(
        api_url=api_url,
        x_token=x_token,
        profile_id=profile_id,
    )


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
    x_token = decrypt(row.x_token_encrypted or "")
    runtime_status = await _build_vision_runtime_status(
        api_url=row.api_url,
        x_token=x_token,
        profile_id=row.profile_id,
    )
    return VisionSettingsSchema(
        api_url=row.api_url,
        x_token="",  # Никогда не возвращаем расшифрованный токен
        profile_id=row.profile_id,
        has_token=bool(row.x_token_encrypted),
        auto_restart_on_missing_cdp=settings.vision_auto_restart_on_missing_cdp,
        runtime_status=str(runtime_status["runtime_status"]),
        runtime_status_message=str(runtime_status["runtime_status_message"]),
        profile_running=bool(runtime_status["profile_running"]),
        cdp_port=runtime_status["cdp_port"],
        cdp_ready=bool(runtime_status["cdp_ready"]),
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
    x_token = body.x_token or decrypt(row.x_token_encrypted or "")
    runtime_status = await _build_vision_runtime_status(
        api_url=row.api_url,
        x_token=x_token,
        profile_id=row.profile_id,
    )
    return VisionSettingsSchema(
        api_url=row.api_url,
        x_token="",
        profile_id=row.profile_id,
        has_token=bool(row.x_token_encrypted),
        auto_restart_on_missing_cdp=settings.vision_auto_restart_on_missing_cdp,
        runtime_status=str(runtime_status["runtime_status"]),
        runtime_status_message=str(runtime_status["runtime_status_message"]),
        profile_running=bool(runtime_status["profile_running"]),
        cdp_port=runtime_status["cdp_port"],
        cdp_ready=bool(runtime_status["cdp_ready"]),
    )


@router.post("/vision/ensure-cdp", response_model=VisionCdpEnsureResponseSchema)
async def vision_ensure_cdp(db: AsyncSession = Depends(get_db)):
    """Мягко восстановить CDP только если профиль уже запущен без рабочего порта."""
    settings = get_settings()
    result = await db.execute(
        select(VisionSettings).where(VisionSettings.singleton_key == "default")
    )
    row = result.scalar_one_or_none()
    if row is None:
        return VisionCdpEnsureResponseSchema(
            status="NOT_CONFIGURED",
            message="Vision ещё не настроен, bootstrap CDP пропущен.",
            action="skip",
        )

    if not row.x_token_encrypted:
        return VisionCdpEnsureResponseSchema(
            status="NOT_CONFIGURED",
            message="Vision X-Token не настроен, bootstrap CDP пропущен.",
            action="skip",
        )
    if not row.profile_id:
        return VisionCdpEnsureResponseSchema(
            status="NOT_CONFIGURED",
            message="Профиль Vision не выбран, bootstrap CDP пропущен.",
            action="skip",
        )

    x_token = decrypt(row.x_token_encrypted)
    if not x_token:
        return VisionCdpEnsureResponseSchema(
            ok=False,
            status="NOT_CONFIGURED",
            message="Не удалось расшифровать Vision X-Token.",
            action="skip",
        )

    runtime_status = await _build_vision_runtime_status(
        api_url=row.api_url,
        x_token=x_token,
        profile_id=row.profile_id,
    )
    status = str(runtime_status["runtime_status"])
    if status == "READY":
        return VisionCdpEnsureResponseSchema(
            status=status,
            message=str(runtime_status["runtime_status_message"]),
            action="none",
            profile_running=bool(runtime_status["profile_running"]),
            cdp_port=runtime_status["cdp_port"],
            cdp_ready=bool(runtime_status["cdp_ready"]),
        )

    if status == "NOT_RUNNING":
        return VisionCdpEnsureResponseSchema(
            status=status,
            message=(
                "Профиль Vision не запущен. Он будет запущен лениво при первом скане "
                "или обращении к браузеру."
            ),
            action="lazy_start",
            profile_running=False,
        )

    if status == "API_UNAVAILABLE":
        return VisionCdpEnsureResponseSchema(
            ok=False,
            status=status,
            message=str(runtime_status["runtime_status_message"]),
            action="skip",
        )

    if status not in {"MISSING_CDP", "CDP_NOT_READY"}:
        return VisionCdpEnsureResponseSchema(
            status=status,
            message=str(runtime_status["runtime_status_message"]),
            action="none",
            profile_running=bool(runtime_status["profile_running"]),
            cdp_port=runtime_status["cdp_port"],
            cdp_ready=bool(runtime_status["cdp_ready"]),
        )

    if not settings.vision_auto_restart_on_missing_cdp:
        return VisionCdpEnsureResponseSchema(
            ok=False,
            status=status,
            message=("Профиль Vision запущен без рабочего CDP, но автоперезапуск отключён."),
            action="skip",
            profile_running=bool(runtime_status["profile_running"]),
            cdp_port=runtime_status["cdp_port"],
            cdp_ready=False,
        )

    folder_id = str(runtime_status.get("folder_id") or "")
    if not folder_id:
        return VisionCdpEnsureResponseSchema(
            ok=False,
            status=status,
            message="Vision не вернул folder_id для запущенного профиля, автоперезапуск невозможен.",
            action="skip",
            profile_running=True,
            cdp_port=runtime_status["cdp_port"],
            cdp_ready=False,
        )

    await _vision_request(row.api_url, x_token, f"/stop/{folder_id}/{row.profile_id}")
    stopped = await _wait_vision_profile_stopped(
        api_url=row.api_url,
        x_token=x_token,
        profile_id=row.profile_id,
    )
    if not stopped:
        return VisionCdpEnsureResponseSchema(
            ok=False,
            status="RECOVERY_FAILED",
            message=f"Vision не остановил профиль {row.profile_id} перед восстановлением CDP.",
            action="restart_failed",
            profile_running=True,
            cdp_port=runtime_status["cdp_port"],
            cdp_ready=False,
        )

    await asyncio.sleep(_VISION_RECOVERY_SETTLE_SECONDS)
    port = await _start_vision_profile(
        api_url=row.api_url,
        x_token=x_token,
        folder_id=folder_id,
        profile_id=row.profile_id,
    )
    if port is None:
        return VisionCdpEnsureResponseSchema(
            ok=False,
            status="RECOVERY_FAILED",
            message="Профиль Vision перезапущен, но CDP-порт не появился.",
            action="restart",
            profile_running=True,
            cdp_ready=False,
        )

    cdp_ready = await _wait_cdp_ready(port)
    return VisionCdpEnsureResponseSchema(
        ok=cdp_ready,
        status="READY" if cdp_ready else "CDP_NOT_READY",
        message=(
            f"Профиль Vision перезапущен, CDP-порт {port} готов."
            if cdp_ready
            else f"Профиль Vision перезапущен, но CDP-порт {port} пока не отвечает."
        ),
        action="restart",
        profile_running=True,
        cdp_port=port,
        cdp_ready=cdp_ready,
    )


@router.post("/vision/reconnect")
async def vision_reconnect(db: AsyncSession = Depends(get_db)):
    """Немедленно перезапустить профиль Vision и попросить observer переподключиться."""
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

    async def _resolve_folder_id() -> str:
        data = await _vision_request(row.api_url, x_token, "/list")
        profiles = data.get("profiles", []) if data else []
        for p in profiles:
            if p.get("profile_id") == row.profile_id:
                return p["folder_id"]
        raise HTTPException(status_code=404, detail=f"Profile {row.profile_id} not found")

    async def _stop_profile(folder_id: str) -> None:
        try:
            await _vision_request(row.api_url, x_token, f"/stop/{folder_id}/{row.profile_id}")
        except HTTPException as exc:
            if exc.status_code == 401:
                raise
            pass  # Профиль мог уже остановиться или исчезнуть из списка Vision.

    async def _wait_stopped(timeout_secs: float = 15.0) -> bool:
        import asyncio

        deadline = __import__("time").time() + timeout_secs
        while __import__("time").time() < deadline:
            data = await _vision_request(row.api_url, x_token, "/list")
            profiles = data.get("profiles", []) if data else []
            if not any(p.get("profile_id") == row.profile_id for p in profiles):
                return True
            await asyncio.sleep(1.0)
        return False

    async def _start_profile(folder_id: str) -> int | None:
        import asyncio

        data = await _vision_request(row.api_url, x_token, f"/start/{folder_id}/{row.profile_id}")
        port = data.get("port") or data.get("cdp_port") if data else None
        if port:
            return port
        # Poll for port
        deadline = __import__("time").time() + 15.0
        while __import__("time").time() < deadline:
            data = await _vision_request(row.api_url, x_token, "/list")
            profiles = data.get("profiles", []) if data else []
            for p in profiles:
                if p.get("profile_id") == row.profile_id and p.get("port"):
                    return p["port"]
            await asyncio.sleep(1.0)
        return None

    profile_port: int | None = None
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

    if reconnect_error is not None:
        raise reconnect_error

    if profile_port is not None:
        return {
            "ok": True,
            "message": (
                "Профиль Vision перезапущен. Observer переподключится автоматически "
                "на следующем цикле."
            ),
            "port": profile_port,
            "observer_reconnect_requested": True,
        }

    return {
        "ok": True,
        "message": (
            "Профиль Vision перезапущен, но CDP-порт пока не появился. "
            "Observer переподключится автоматически на следующем цикле."
        ),
        "observer_reconnect_requested": True,
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
        if resp.status_code == 401:
            raise HTTPException(
                status_code=401,
                detail="Vision X-Token недействителен или истёк. Обновите X-Token в настройках Vision.",
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

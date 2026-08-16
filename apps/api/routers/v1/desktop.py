# -*- coding: utf-8 -*-
"""Данные нативного канала к рабочему столу Vision.

Веб-канал (KasmVNC c билетами и /desktop/launch) демонтирован: браузер на
iPhone не может отдать системный буфер обмена — WebKit требует свежего жеста
и считает его протухшим после любого await. Доступ к столу идёт нативным
RustDesk через собственный брокер, а этот эндпоинт отдаёт оператору всё,
что нужно ввести в клиент: адрес брокера, его публичный ключ и ID стола.

Секретов здесь нет: пароль канала в ответ не попадает никогда — его задаёт
владелец при деплое, и клиент запоминает его после первого подключения.
"""

from __future__ import annotations

import json
import logging
import secrets

from fastapi import APIRouter, HTTPException, Request, Response

from apps.api.deps import DepEngine, DepSettings
from apps.api.routers.panel_auth import resolve_panel_session
from apps.api.routers.v1.schemas.desktop import DesktopNativeChannelResponse
from apps.api.routers.v1.tma import get_tma_principal
from core.config import reveal_secret

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/desktop", tags=["desktop"])

_PANEL_PRODUCTION_ORIGIN = "https://app.adpulse.su"
_NO_STORE = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
}


async def _resolve_owner_identity(
    request: Request,
    engine: DepEngine,
    settings: DepSettings,
) -> None:
    """Данные канала видит только владелец — тем же гейтом, что и запуск."""
    authorization = request.headers.get("authorization") or ""
    if authorization.startswith("Bearer "):
        principal = await get_tma_principal(request, engine, settings)
        if not principal.is_owner:
            raise HTTPException(status_code=403, detail="Рабочий стол доступен только владельцу")
        return

    if request.headers.get("origin") not in (None, _PANEL_PRODUCTION_ORIGIN):
        raise HTTPException(status_code=403, detail="Недопустимый Origin")
    expected_key = reveal_secret(settings.api_key).strip()
    provided_key = request.headers.get("x-api-key") or ""
    if not expected_key:
        raise HTTPException(status_code=503, detail="API_KEY не сконфигурирован на сервере")
    if not provided_key or not secrets.compare_digest(provided_key, expected_key):
        raise HTTPException(status_code=401, detail="Требуется корректный X-API-Key")
    resolved = await resolve_panel_session(request, engine, settings)
    if resolved is None:
        raise HTTPException(status_code=401, detail="Требуется вход через Telegram")


def _read_channel_info(settings: DepSettings) -> dict[str, str | None]:
    """Прочитать то, что стол опубликовал в readiness-каталог.

    Файл пишет entrypoint стола: адрес и ключ — сразу после старта канала,
    ID устройства — как только его выдаст брокер. Файла нет — стол ещё не
    поднялся после деплоя, и это состояние называется вслух, а не маскируется
    пустыми значениями.
    """
    path = settings.desktop_native_channel_path
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"server": None, "key": None, "device_id": None}
    except OSError:
        logger.warning("Не удалось прочитать данные нативного канала: %s", path)
        return {"server": None, "key": None, "device_id": None}
    try:
        payload = json.loads(raw)
    except ValueError:
        logger.warning("Данные нативного канала повреждены: %s", path)
        return {"server": None, "key": None, "device_id": None}

    def text(value: object) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None

    return {
        "server": text(payload.get("server")),
        "key": text(payload.get("key")),
        "device_id": text(payload.get("device_id")),
    }


@router.get(
    "/native",
    response_model=DesktopNativeChannelResponse,
    responses={
        401: {"description": "Authentication failed"},
        403: {"description": "Not an active owner or invalid origin"},
    },
)
async def get_native_channel(
    request: Request,
    response: Response,
    engine: DepEngine,
    settings: DepSettings,
) -> DesktopNativeChannelResponse:
    """Адрес брокера, его публичный ключ и ID стола для клиента RustDesk."""
    response.headers.update(_NO_STORE)
    await _resolve_owner_identity(request, engine, settings)
    info = _read_channel_info(settings)
    return DesktopNativeChannelResponse(
        available=bool(info["server"] and info["device_id"]),
        server=info["server"],
        key=info["key"],
        device_id=info["device_id"],
    )


__all__ = ["router"]

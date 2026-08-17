# -*- coding: utf-8 -*-
"""Данные нативного канала к рабочему столу Vision.

Веб-канал (KasmVNC c билетами и /desktop/launch) демонтирован: браузер на
iPhone не может отдать системный буфер обмена — WebKit требует свежего жеста
и считает его протухшим после любого await. Доступ к столу идёт нативным
RustDesk через собственный брокер, а этот эндпоинт отдаёт оператору всё,
что нужно ввести в клиент: адрес брокера, его публичный ключ и ID стола.

`/native` секретов не содержит: он рендерится в разметку страницы и живёт в
ней, пока экран открыт. Пароль канала отдаёт только `/native/launch` — готовой
ссылкой запуска, по явному нажатию владельца и без следа в HTML.
"""

from __future__ import annotations

import json
import logging
import secrets
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response

from apps.api.deps import DepEngine, DepSettings
from apps.api.routers.panel_auth import resolve_panel_session
from apps.api.routers.v1.schemas.desktop import (
    DesktopLaunchLinkResponse,
    DesktopNativeChannelResponse,
)
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


@router.post(
    "/native/launch",
    response_model=DesktopLaunchLinkResponse,
    responses={
        401: {"description": "Authentication failed"},
        403: {"description": "Not an active owner or invalid origin"},
        409: {"description": "Desktop has not published its channel id yet"},
        503: {"description": "Channel password is not configured"},
    },
)
async def get_native_launch_link(
    request: Request,
    response: Response,
    engine: DepEngine,
    settings: DepSettings,
) -> DesktopLaunchLinkResponse:
    """Ссылка запуска клиента RustDesk: ID стола и пароль канала.

    Отдельная ручка, а не поле в `/native`, ровно ради того, чтобы пароль не
    оседал в разметке открытого экрана: он покидает сервер только по явному
    нажатию владельца и живёт в памяти вкладки ровно до открытия приложения.

    POST, а не GET, хотя ничего не меняет: ответ несёт секрет, а GET браузеры и
    прокси считают безопасным для предзагрузки и кэша.

    Ссылка НЕ несёт адрес брокера и ключ — схема `rustdesk://` их не принимает.
    Клиент нужно один раз переключить на наш брокер, и экран стола этот шаг
    показывает первым.
    """
    response.headers.update(_NO_STORE)
    await _resolve_owner_identity(request, engine, settings)
    info = _read_channel_info(settings)
    device_id = info["device_id"]
    if not device_id:
        # Стол ещё не поднялся после деплоя. Называем это состояние вслух:
        # ссылка с пустым ID открыла бы приложение в никуда.
        raise HTTPException(status_code=409, detail="Стол ещё не опубликовал ID канала")
    password = reveal_secret(settings.desktop_rustdesk_password).strip()
    if not password:
        raise HTTPException(status_code=503, detail="Пароль канала не сконфигурирован на сервере")
    # quote со стандартным safe="/" оставил бы слеш сырым, а он в query-строке
    # значим не для всех клиентов; пароль генерируется нами и может его нести.
    #
    # Форма каноничная, а не короткая. `rustdesk://<id>?password=` клиент
    # разбирает наполовину: на холодном старте пароль доезжает, а на уже
    # запущенном приложении подставляется только ID — оба состояния замерены на
    # живом канале. Из-за этого кнопка работала через раз, а оператор видел
    # «Wrong password» без единой подсказки, что дело в форме ссылки.
    return DesktopLaunchLinkResponse(
        url=(
            f"rustdesk://connection/new/{quote(device_id, safe='')}"
            f"?password={quote(password, safe='')}"
        )
    )


__all__ = ["router"]

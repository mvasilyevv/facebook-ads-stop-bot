# -*- coding: utf-8 -*-
"""FastAPI роутер для настроек Vision (settings_vision).

Endpoints под /api (благодаря auto-discovery с prefix="/api"):
- GET  /settings/vision   — VisionConfig + runtime-поля из Redis
- PUT  /settings/vision   — обновить x_token / profile_id
- POST /vision/reconnect  — gRPC ReconnectBrowser к browser-agent
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import DepEngine, DepRedis, DepSettings
from apps.api.routers.v1.schemas.settings_vision import (
    VisionReconnectResponse,
    VisionSettingsResponse,
    VisionSettingsUpdateRequest,
)
from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig
from core.models.settings.vision_config import VisionConfig

logger = logging.getLogger(__name__)

# Роутер для /settings/vision
_settings_router = APIRouter(prefix="/settings/vision", tags=["settings"])

# Роутер для /vision (reconnect)
_vision_router = APIRouter(prefix="/vision", tags=["settings"])


# Redis-ключ heartbeat browser-agent.
_BROWSER_AGENT_HEARTBEAT_KEY = "worker:heartbeat:browser-agent"


# ---------------------------------------------------------------------------
# Snapshot — безопасная передача данных вне session
# ---------------------------------------------------------------------------


@dataclass
class _VisionSnapshot:
    """Скалярные поля VisionConfig без ORM-ленивой загрузки."""

    x_token_encrypted: str | None
    profile_id: str | None
    auto_restart_on_missing_cdp: bool


def _snapshot(config: VisionConfig | None) -> _VisionSnapshot | None:
    """Считывает нужные поля из ORM-объекта ВНУТРИ session и возвращает скаляры."""
    if config is None:
        return None
    return _VisionSnapshot(
        x_token_encrypted=config.x_token_encrypted,
        profile_id=config.profile_id,
        auto_restart_on_missing_cdp=bool(config.auto_restart_on_missing_cdp),
    )


async def _load_config(session: AsyncSession) -> VisionConfig | None:
    """Читает singleton VisionConfig или возвращает None, если строки нет."""
    return await session.scalar(select(VisionConfig).where(VisionConfig.singleton_key == "default"))


async def _read_runtime_from_redis(redis: object) -> dict[str, object]:
    """Считывает runtime-поля браузера из Redis heartbeat-ключа.

    Если ключ отсутствует — возвращает дефолтный словарь с null/False значениями.
    Heartbeat может содержать JSON или просто строку — пробуем распарсить.
    """
    defaults: dict[str, object] = {
        "runtime_status": None,
        "runtime_status_message": None,
        "cdp_ready": False,
        "cdp_port": None,
    }
    try:
        value = await redis.get(_BROWSER_AGENT_HEARTBEAT_KEY)
    except Exception as exc:
        logger.warning("Ошибка чтения Redis heartbeat browser-agent: %s", exc)
        return defaults

    if value is None:
        return defaults

    # Пробуем распарсить JSON-payload.
    try:
        data = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        # Строка не JSON — считаем, что это просто строка-статус.
        return {
            "runtime_status": "ONLINE",
            "runtime_status_message": str(value),
            "cdp_ready": False,
            "cdp_port": None,
        }

    return {
        "runtime_status": data.get("status") or "ONLINE",
        "runtime_status_message": data.get("message") or data.get("detail"),
        "cdp_ready": bool(data.get("cdp_ready", False)),
        "cdp_port": data.get("cdp_port"),
    }


# ---------------------------------------------------------------------------
# /settings/vision
# ---------------------------------------------------------------------------


@_settings_router.get("", response_model=VisionSettingsResponse)
async def get_vision_settings(
    engine: DepEngine,
    redis: DepRedis,
) -> VisionSettingsResponse:
    """Возвращает VisionConfig и runtime-поля из Redis.

    has_token = x_token_encrypted не пустой.
    Runtime-поля берутся из worker:heartbeat:browser-agent.
    """
    async with AsyncSession(engine) as session:
        config = await _load_config(session)
        snap = _snapshot(config)

    runtime = await _read_runtime_from_redis(redis)

    has_token = bool(snap and snap.x_token_encrypted)
    profile_id: str | None = None
    if snap and snap.profile_id:
        profile_id = snap.profile_id

    return VisionSettingsResponse(
        has_token=has_token,
        profile_id=profile_id,
        auto_restart_on_missing_cdp=snap.auto_restart_on_missing_cdp if snap else True,
        runtime_status=runtime["runtime_status"],  # type: ignore[arg-type]
        runtime_status_message=runtime["runtime_status_message"],  # type: ignore[arg-type]
        cdp_ready=bool(runtime["cdp_ready"]),
        cdp_port=runtime["cdp_port"],  # type: ignore[arg-type]
    )


@_settings_router.put("", response_model=VisionSettingsResponse)
async def put_vision_settings(
    body: VisionSettingsUpdateRequest,
    engine: DepEngine,
    redis: DepRedis,
) -> VisionSettingsResponse:
    """Обновляет x_token / profile_id / флаг self-heal в VisionConfig singleton.

    Если x_token передан — шифрует и сохраняет.
    Если profile_id передан — обновляет.
    Если auto_restart_on_missing_cdp передан — выставляет флаг (None = не трогать).
    Если строки ещё нет — создаёт с server-defaults.
    """
    from core.crypto import encrypt

    async with AsyncSession(engine) as session:
        config = await _load_config(session)
        if config is None:
            config = VisionConfig(
                x_token_encrypted="",
                profile_id="",
            )
            session.add(config)

        if body.x_token is not None:
            config.x_token_encrypted = encrypt(body.x_token) if body.x_token else ""
        if body.profile_id is not None:
            config.profile_id = body.profile_id
        if body.auto_restart_on_missing_cdp is not None:
            config.auto_restart_on_missing_cdp = body.auto_restart_on_missing_cdp

        await session.flush()
        await session.refresh(config)
        snap = _snapshot(config)
        await session.commit()

    runtime = await _read_runtime_from_redis(redis)

    has_token = bool(snap and snap.x_token_encrypted)
    profile_id_val: str | None = None
    if snap and snap.profile_id:
        profile_id_val = snap.profile_id

    return VisionSettingsResponse(
        has_token=has_token,
        profile_id=profile_id_val,
        auto_restart_on_missing_cdp=snap.auto_restart_on_missing_cdp if snap else True,
        runtime_status=runtime["runtime_status"],  # type: ignore[arg-type]
        runtime_status_message=runtime["runtime_status_message"],  # type: ignore[arg-type]
        cdp_ready=bool(runtime["cdp_ready"]),
        cdp_port=runtime["cdp_port"],  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# /vision/reconnect
# ---------------------------------------------------------------------------


@_vision_router.post("/reconnect", response_model=VisionReconnectResponse)
async def post_vision_reconnect(
    engine: DepEngine,
    settings: DepSettings,
) -> VisionReconnectResponse:
    """Триггерит gRPC ReconnectBrowser к browser-agent.

    Читает x_token и profile_id из БД (или fallback в Settings).
    Возвращает 503 при недоступности gRPC.
    """
    import grpc

    from core.crypto import decrypt

    # Читаем токен и профиль из БД.
    async with AsyncSession(engine) as session:
        config = await _load_config(session)
        snap = _snapshot(config)

    x_token = settings.vision_x_token
    profile_id = settings.vision_profile_id
    api_url = settings.vision_api_url

    if snap:
        if snap.x_token_encrypted:
            try:
                x_token = decrypt(snap.x_token_encrypted)
            except Exception as exc:
                logger.warning("Не удалось расшифровать vision x_token: %s", exc)
        if snap.profile_id:
            profile_id = snap.profile_id

    client = BrowserAgentClient(
        BrowserAgentConfig(
            vision_x_token=x_token,
            vision_api_url=api_url,
            vision_profile_id=profile_id,
        )
    )
    try:
        await client.start()
        await client.reconnect_browser()
    except grpc.RpcError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"gRPC browser-agent недоступен: {exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Ошибка переподключения к browser-agent: {exc}",
        ) from exc
    finally:
        try:
            await client.close()
        except Exception:
            pass

    return VisionReconnectResponse(status="reconnected")


# ---------------------------------------------------------------------------
# Экспорт единого router
# ---------------------------------------------------------------------------

# auto-discovery ищет атрибут `router` в модуле — объединяем sub-router'ы.
router = APIRouter(tags=["settings"])
router.include_router(_settings_router)
router.include_router(_vision_router)

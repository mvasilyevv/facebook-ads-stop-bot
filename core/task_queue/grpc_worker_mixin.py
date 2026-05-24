# -*- coding: utf-8 -*-
"""Общие утилиты подключения Vision/gRPC для всех воркеров с browser-agent."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig
from core.config import get_settings
from core.crypto import decrypt
from core.db import get_session_factory
from core.models import VisionSettings

logger = logging.getLogger(__name__)

_GRPC_DISCONNECT_TIMEOUT_SECONDS = 15
_GRPC_CLOSE_TIMEOUT_SECONDS = 10


async def load_vision_settings() -> tuple[str, str, str]:
    """Загружает Vision-настройки из БД с fallback на .env.

    Returns:
        Кортеж (x_token, api_url, profile_id).
    """
    settings = get_settings()
    factory = get_session_factory()
    try:
        async with factory() as session:
            result = await session.execute(
                select(VisionSettings).where(VisionSettings.singleton_key == "default")
            )
            row = result.scalar_one_or_none()
            if row and row.x_token_encrypted and row.profile_id:
                token = decrypt(row.x_token_encrypted)
                if token:
                    logger.info("Vision-настройки загружены из БД")
                    return token, row.api_url or settings.vision_api_url, row.profile_id
    except Exception:
        logger.debug("Не удалось загрузить Vision-настройки из БД", exc_info=True)

    return settings.vision_x_token, settings.vision_api_url, settings.vision_profile_id


def build_client_config(
    vision_token: str, vision_url: str, vision_profile: str
) -> BrowserAgentConfig:
    """Создаёт конфигурацию gRPC клиента из Vision-настроек."""
    settings = get_settings()
    return BrowserAgentConfig(
        vision_x_token=vision_token,
        vision_api_url=vision_url,
        vision_profile_id=vision_profile,
        vision_folder_id=getattr(settings, "vision_folder_id", None),
    )


async def init_grpc_client(
    vision_token: str,
    vision_url: str,
    vision_profile: str,
    *,
    worker_name: str = "worker",
) -> BrowserAgentClient:
    """Создаёт, запускает и подключает gRPC клиент к browser-agent."""
    config = build_client_config(vision_token, vision_url, vision_profile)
    client = BrowserAgentClient(config)
    await client.start()
    await client.start_browser()
    logger.info("%s: gRPC клиент подключён, session_id=%s", worker_name, client.session_id)
    return client


async def close_grpc_client(
    grpc_client: BrowserAgentClient | None,
    *,
    worker_name: str = "worker",
    disconnect_timeout: float = _GRPC_DISCONNECT_TIMEOUT_SECONDS,
    close_timeout: float = _GRPC_CLOSE_TIMEOUT_SECONDS,
) -> None:
    """Закрывает gRPC канал с таймаутами без остановки браузер-профиля."""
    if grpc_client is None:
        return
    try:
        await asyncio.wait_for(grpc_client.disconnect_browser(), timeout=disconnect_timeout)
    except asyncio.TimeoutError:
        logger.warning(
            "%s: таймаут %s сек при отключении от browser-agent",
            worker_name,
            disconnect_timeout,
        )
    except Exception:
        logger.debug("%s: не удалось отключиться от browser-agent", worker_name, exc_info=True)
    try:
        await asyncio.wait_for(grpc_client.close(), timeout=close_timeout)
    except asyncio.TimeoutError:
        logger.warning("%s: таймаут %s сек при закрытии gRPC канала", worker_name, close_timeout)
    except Exception:
        logger.debug("%s: не удалось закрыть gRPC канал", worker_name, exc_info=True)
